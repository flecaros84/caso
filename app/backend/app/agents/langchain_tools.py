# app/backend/app/agents/langchain_tools.py

from __future__ import annotations

import json
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.agent_memory import AgentMemory
from app.models.schemas import CandidateEvaluation, Competency
from app.services.competency_service import CompetencyService
from app.services.evaluator_service import EvaluatorService
from app.services.file_service import FileService
from app.services.rag_service import SimpleRAGIndex
from app.services.ranking_service import RankingService
from app.services.report_service import ReportService
from app.services.text_extractor import TextExtractor


# Define los datos de entrada que necesita la herramienta para extraer texto de un anuncio.
class ExtractAnnouncementInput(BaseModel):
    announcement_id: str = Field(description="ID del anuncio laboral seleccionado.")
    announcement_text_override: str | None = Field(
        default=None,
        description="Texto manual opcional del anuncio laboral. Si existe, evita OCR.",
    )


# Define los datos de entrada que necesita la herramienta para extraer texto de un CV.
class ExtractCVInput(BaseModel):
    cv_id: str = Field(description="ID del CV seleccionado.")


# Define los datos de entrada para deducir competencias desde el texto del anuncio.
class CompetencyInput(BaseModel):
    announcement_text: str = Field(description="Texto completo del anuncio laboral.")


# Define los datos de entrada para evaluar un candidato contra una lista de competencias.
class CandidateEvaluationInput(BaseModel):
    cv_id: str = Field(description="ID del CV candidato.")
    cv_text: str = Field(description="Texto extraído del CV.")
    competencies_json: str = Field(description="Lista de competencias en formato JSON.")


# Define los datos de entrada para generar ranking y terna recomendada.
class RankingInput(BaseModel):
    candidate_results_json: str = Field(description="Lista de resultados de candidatos en formato JSON.")
    terna_size: int = Field(default=3, ge=1, le=10, description="Cantidad de candidatos en la terna.")


# Define los datos de entrada necesarios para generar reportes del análisis.
class ReportInput(BaseModel):
    analysis_response_json: str = Field(description="Resultado final del análisis en formato JSON.")
    job_id: str | None = Field(default=None, description="ID del trabajo de análisis, si existe.")
    announcement_id: str = Field(description="ID del anuncio laboral analizado.")


# Define los datos de entrada para guardar el resumen final en memoria del agente.
class MemoryInput(BaseModel):
    session_summary_json: str = Field(description="Resumen final de la sesión del agente en formato JSON.")


class RecruitmentLangChainTools:
    """
    Builds LangChain tools backed by the existing application services.

    Tool categories:
    - consultation: document extraction and RAG retrieval.
    - reasoning: competency extraction, candidate evaluation and ranking.
    - writing: report generation.
    - memory: long-term memory persistence.
    """

    def __init__(
        self,
        *,
        file_service: FileService,
        text_extractor: TextExtractor,
        competency_service: CompetencyService,
        evaluator_service: EvaluatorService,
        ranking_service: RankingService,
        report_service: ReportService,
        memory: AgentMemory,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        # Guarda los servicios internos que serán utilizados por las herramientas LangChain.
        self.file_service = file_service
        self.text_extractor = text_extractor
        self.competency_service = competency_service
        self.evaluator_service = evaluator_service
        self.ranking_service = ranking_service
        self.report_service = report_service

        # Guarda la memoria del agente para registrar decisiones, llamadas y contexto.
        self.memory = memory

        # Callback opcional para enviar avances al frontend durante procesos largos.
        self.progress_callback = progress_callback

    def build(self) -> list[StructuredTool]:
        # Construye y devuelve la lista de herramientas disponibles para el agente LangChain.
        # Cada StructuredTool conecta una función interna con un esquema de argumentos validado por Pydantic.
        return [
            StructuredTool.from_function(
                name="extract_announcement_text",
                description=(
                    "Consulta y extrae el texto del anuncio laboral. "
                    "Usa texto manual si existe; si no, extrae desde archivo con OCR o lectura directa."
                ),
                func=self.extract_announcement_text,
                args_schema=ExtractAnnouncementInput,
            ),
            StructuredTool.from_function(
                name="extract_cv_text",
                description="Consulta y extrae el texto de un CV seleccionado desde los archivos locales.",
                func=self.extract_cv_text,
                args_schema=ExtractCVInput,
            ),
            StructuredTool.from_function(
                name="extract_competencies",
                description=(
                    "Herramienta de razonamiento. Deduce competencias laborales desde el anuncio "
                    "usando LLM o fallback local."
                ),
                func=self.extract_competencies,
                args_schema=CompetencyInput,
            ),
            StructuredTool.from_function(
                name="evaluate_candidate_with_rag",
                description=(
                    "Herramienta de consulta semántica y razonamiento. Construye un índice RAG "
                    "del CV, recupera evidencia y evalúa competencias."
                ),
                func=self.evaluate_candidate_with_rag,
                args_schema=CandidateEvaluationInput,
            ),
            StructuredTool.from_function(
                name="rank_candidates",
                description=(
                    "Herramienta de razonamiento y cálculo. Ordena candidatos por puntaje "
                    "y genera la terna recomendada."
                ),
                func=self.rank_candidates,
                args_schema=RankingInput,
            ),
            StructuredTool.from_function(
                name="write_analysis_report",
                description="Herramienta de escritura. Genera reportes locales en formato Markdown y JSON.",
                func=self.write_analysis_report,
                args_schema=ReportInput,
            ),
            StructuredTool.from_function(
                name="save_agent_memory",
                description="Herramienta de memoria. Guarda un resumen de la sesión en memoria persistente JSON.",
                func=self.save_agent_memory,
                args_schema=MemoryInput,
            ),
        ]

    def extract_announcement_text(
        self,
        announcement_id: str,
        announcement_text_override: str | None = None,
    ) -> str:
        # Limpia el texto manual del anuncio, si fue ingresado desde el frontend.
        manual_text = (announcement_text_override or "").strip()

        # Si existe texto manual, se usa directamente y se evita OCR o lectura desde archivo.
        if manual_text:
            text = manual_text
            self.memory.add_decision(
                decision="select_announcement_source",
                reason="The request includes manual announcement text.",
                outcome="Manual text was used and OCR/file extraction was skipped.",
            )
        else:
            # Si no hay texto manual, se resuelve el archivo del anuncio y se extrae su contenido.
            path = self.file_service.resolve_announcement(announcement_id)
            text = self.text_extractor.extract(path)
            self.memory.add_decision(
                decision="select_announcement_source",
                reason="The request does not include manual announcement text.",
                outcome=f"Announcement text was extracted from file: {path.name}.",
            )

        # Registra la llamada a la herramienta para trazabilidad.
        self.memory.add_tool_call(
            tool_name="extract_announcement_text",
            input_summary=announcement_id,
            output_summary=f"{len(text)} characters extracted.",
            success=bool(text.strip()),
        )

        return text

    def extract_cv_text(self, cv_id: str) -> str:
        # Resuelve la ruta local del CV seleccionado.
        path = self.file_service.resolve_cv(cv_id)

        # Extrae el texto del CV usando el servicio de extracción documental.
        text = self.text_extractor.extract(path)

        # Registra la extracción del CV en la memoria del agente.
        self.memory.add_tool_call(
            tool_name="extract_cv_text",
            input_summary=cv_id,
            output_summary=f"{path.name}: {len(text)} characters extracted.",
            success=bool(text.strip()),
        )

        return text

    def extract_competencies(self, announcement_text: str) -> str:
        # Deduce competencias laborales desde el texto del anuncio.
        # El servicio puede usar LLM o fallback local, según la configuración y disponibilidad.
        competencies = self.competency_service.extract_competencies(announcement_text)

        # Obtiene el modo usado para la extracción, por ejemplo LLM, fallback local o desconocido.
        mode = getattr(self.competency_service, "last_extraction_mode", "unknown")

        # Registra una decisión del agente indicando cómo se generaron los criterios de evaluación.
        self.memory.add_decision(
            decision="extract_competencies",
            reason="The agent must transform the announcement into auditable evaluation criteria.",
            outcome=f"{len(competencies)} competencies were generated using mode: {mode}.",
        )

        # Registra la llamada a la herramienta de extracción de competencias.
        self.memory.add_tool_call(
            tool_name="extract_competencies",
            input_summary="announcement_text",
            output_summary=f"{len(competencies)} competencies; mode={mode}.",
            success=len(competencies) > 0,
        )

        # Devuelve las competencias como JSON para que puedan ser usadas por otras herramientas.
        return json.dumps(
            [competency.model_dump(mode="json") for competency in competencies],
            ensure_ascii=False,
        )

    def evaluate_candidate_with_rag(
        self,
        cv_id: str,
        cv_text: str,
        competencies_json: str,
    ) -> str:
        # Resuelve la ruta local del CV para obtener su nombre y asociarlo al candidato.
        path = self.file_service.resolve_cv(cv_id)

        # Convierte el JSON de competencias en objetos Competency validados.
        raw_competencies = json.loads(competencies_json)
        competencies = [Competency(**item) for item in raw_competencies]

        # Construye un índice RAG simple para el CV del candidato.
        # Este índice permite recuperar fragmentos relevantes del CV según cada competencia.
        rag_index = SimpleRAGIndex(
            candidate_id=cv_id,
            candidate_name=path.name,
            text=cv_text,
        )

        # Evalúa el candidato usando el índice RAG y la lista de competencias.
        # El servicio evaluador recupera evidencia y la analiza con LLM o fallback local.
        evaluations = self.evaluator_service.evaluate_candidate(
            rag_index,
            competencies,
            progress_callback=self.progress_callback,
        )

        # Construye el resultado final del candidato, incluyendo puntaje normalizado y recomendación.
        candidate_result = self.ranking_service.build_candidate_result(
            candidate_id=cv_id,
            candidate_filename=path.name,
            evaluations=evaluations,
        )

        # Guarda un resumen contextual del candidato evaluado en la memoria del agente.
        self.memory.add_context_item(
            source=path.name,
            content_summary=(
                f"Candidate evaluated with {len(evaluations)} competency evaluations. "
                f"Score: {candidate_result.normalized_score}/100."
            ),
            metadata={
                "candidate_id": cv_id,
                "candidate_name": candidate_result.candidate_name,
                "normalized_score": candidate_result.normalized_score,
                "recommendation": candidate_result.recommendation,
            },
        )

        # Registra la llamada de evaluación del candidato para trazabilidad.
        self.memory.add_tool_call(
            tool_name="evaluate_candidate_with_rag",
            input_summary=path.name,
            output_summary=f"score={candidate_result.normalized_score}; recommendation={candidate_result.recommendation}",
            success=True,
        )

        # Devuelve el resultado del candidato como JSON.
        return candidate_result.model_dump_json()

    def rank_candidates(
        self,
        candidate_results_json: str,
        terna_size: int = 3,
    ) -> str:
        # Convierte el JSON de resultados en objetos CandidateEvaluation validados.
        raw_candidates = json.loads(candidate_results_json)
        candidates = [CandidateEvaluation(**item) for item in raw_candidates]

        # Ordena los candidatos según el puntaje calculado por el servicio de ranking.
        ranking = self.ranking_service.rank(candidates)

        # Selecciona la terna recomendada según el tamaño solicitado.
        terna = ranking[:terna_size]

        # Registra si se pudo generar una terna completa o solo una lista parcial.
        if len(ranking) < 3:
            decision = "generate_partial_shortlist"
            reason = "Fewer than three candidates were available."
            outcome = f"A partial shortlist with {len(terna)} candidate(s) was generated."
        else:
            decision = "generate_full_shortlist"
            reason = "Three or more candidates were available."
            outcome = f"A full shortlist with {len(terna)} candidate(s) was generated."

        # Guarda la decisión tomada sobre la generación de la terna.
        self.memory.add_decision(decision=decision, reason=reason, outcome=outcome)

        # Registra la llamada a la herramienta de ranking.
        self.memory.add_tool_call(
            tool_name="rank_candidates",
            input_summary=f"{len(candidates)} candidates.",
            output_summary=f"ranking={len(ranking)}; shortlist={len(terna)}.",
            success=True,
        )

        # Devuelve ranking completo y terna recomendada en formato JSON.
        return json.dumps(
            {
                "ranking": [candidate.model_dump(mode="json") for candidate in ranking],
                "terna": [candidate.model_dump(mode="json") for candidate in terna],
            },
            ensure_ascii=False,
        )

    def write_analysis_report(
        self,
        analysis_response_json: str,
        job_id: str | None,
        announcement_id: str,
    ) -> str:
        # Convierte el resultado final del análisis desde JSON a diccionario Python.
        data = json.loads(analysis_response_json)

        # Genera y guarda los reportes locales del análisis.
        report_info = self.report_service.save_analysis_report(
            result=data,
            job_id=job_id,
            announcement_id=announcement_id,
        )

        # Registra la generación del reporte en la memoria del agente.
        self.memory.add_tool_call(
            tool_name="write_analysis_report",
            input_summary=announcement_id,
            output_summary=report_info.get("markdown_filename", "report generated"),
            success=True,
        )

        # Devuelve la información del reporte generado en formato JSON.
        return json.dumps(report_info, ensure_ascii=False)

    def save_agent_memory(self, session_summary_json: str) -> str:
        # Convierte el resumen final de la sesión desde JSON a diccionario Python.
        session_summary = json.loads(session_summary_json)

        # Guarda el resumen final en la memoria del agente.
        self.memory.set_final_summary(session_summary)

        # Persiste la memoria en almacenamiento local.
        self.memory.save_long_term_memory()

        # Registra la acción de guardado de memoria para trazabilidad.
        self.memory.add_tool_call(
            tool_name="save_agent_memory",
            input_summary="session_summary",
            output_summary=str(self.memory.memory_path),
            success=True,
        )

        # Devuelve la ruta de memoria y el identificador de sesión.
        return json.dumps(
            {
                "memory_path": str(self.memory.memory_path),
                "session_id": self.memory.session_id,
                "saved": True,
            },
            ensure_ascii=False,
        )