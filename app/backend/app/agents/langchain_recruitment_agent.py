# app/backend/app/agents/langchain_recruitment_agent.py

from __future__ import annotations

import json
import os
from typing import Any, Callable

from dotenv import load_dotenv
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from app.agents.agent_memory import AgentMemory
from app.agents.agent_planner import RecruitmentAgentPlanner
from app.agents.langchain_tools import RecruitmentLangChainTools
from app.models.schemas import AnalysisRequest, AnalysisResponse, CandidateEvaluation, Competency
from app.services.competency_service import CompetencyService
from app.services.evaluator_service import EvaluatorService
from app.services.file_service import FileService
from app.services.llm_client import GitHubModelsClient
from app.services.model_catalog_service import ModelCatalogService
from app.services.ranking_service import RankingService
from app.services.report_service import ReportService
from app.services.text_extractor import TextExtractor


class LangChainRecruitmentAgent:
    """
    LangChain-based recruitment screening agent.

    The agent uses LangChain for tool declaration, planning and orchestration traceability.
    The business execution is controlled to keep the candidate evaluation stable and auditable.
    """

    def __init__(
        self,
        *,
        file_service: FileService,
        text_extractor: TextExtractor,
        ranking_service: RankingService,
        report_service: ReportService,
        model_catalog_service: ModelCatalogService,
    ) -> None:
        # Carga las variables de entorno definidas en el archivo .env.
        # Esto permite usar configuraciones como token, endpoint y activación del LLM.
        load_dotenv()

        # Guarda las dependencias principales del agente.
        # Estos servicios ya vienen construidos desde otra parte de la aplicación.
        self.file_service = file_service
        self.text_extractor = text_extractor
        self.ranking_service = ranking_service
        self.report_service = report_service
        self.model_catalog_service = model_catalog_service

        # Inicializa el planificador del agente.
        # Este componente define un flujo base antes de usar la planificación con LangChain.
        self.planner = RecruitmentAgentPlanner()

    def analyze(
        self,
        request: AnalysisRequest,
        *,
        job_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> AnalysisResponse:
        # Crea una memoria para registrar decisiones, pasos, herramientas y trazabilidad.
        memory = AgentMemory()

        # Selecciona el modelo solicitado desde el frontend.
        # Si no se seleccionó un modelo, se usa el modelo por defecto del catálogo.
        selected_model = (request.selected_model or self.model_catalog_service.get_default_model()).strip()

        # Valida que el modelo seleccionado exista dentro del catálogo permitido.
        # Esto evita usar modelos no registrados o no soportados por la aplicación.
        if not self.model_catalog_service.is_allowed_model(selected_model):
            raise ValueError(f"Modelo no permitido o no registrado en github_models.json: {selected_model}")

        # Crea el cliente LLM usando el modelo seleccionado.
        # Este mismo cliente se inyecta en los servicios de competencias y evaluación.
        llm_client = GitHubModelsClient(model=selected_model)
        competency_service = CompetencyService(llm=llm_client)
        evaluator_service = EvaluatorService(llm=llm_client)

        # Construye un plan determinístico de ejecución.
        # Esto entrega un flujo estable antes de intentar cualquier planificación con LLM.
        plan = self.planner.build_plan(
            announcement_name=request.announcement_id,
            manual_announcement_text=request.announcement_text_override,
            cv_names=request.cv_ids,
        )

        # Genera decisiones adaptativas iniciales según el contexto de la solicitud.
        # Estas decisiones se guardan en memoria para mantener trazabilidad.
        adaptive_decisions = self.planner.build_adaptive_decisions(
            announcement_name=request.announcement_id,
            manual_announcement_text=request.announcement_text_override,
            cv_names=request.cv_ids,
        )

        # Guarda las decisiones de planificación en la memoria del agente.
        # Esto permite explicar posteriormente por qué se siguió cierto flujo.
        for decision in adaptive_decisions:
            memory.add_decision(
                decision=decision["decision"],
                reason=decision["reason"],
                outcome=decision["outcome"],
            )

        # Envuelve los servicios internos como herramientas de LangChain.
        # Estas herramientas permiten exponer extracción, evaluación, ranking, reportes y memoria al agente.
        tools_builder = RecruitmentLangChainTools(
            file_service=self.file_service,
            text_extractor=self.text_extractor,
            competency_service=competency_service,
            evaluator_service=evaluator_service,
            ranking_service=self.ranking_service,
            report_service=self.report_service,
            memory=memory,
            progress_callback=progress_callback,
        )

        # Construye la lista de herramientas que LangChain podrá usar durante la etapa de planificación.
        tools = tools_builder.build()

        # Ejecuta la etapa de planificación con LangChain.
        # El LLM solo describe la orquestación; la evaluación real queda controlada por el backend.
        planning_output = self._run_langchain_planning(
            request=request,
            plan=plan,
            decisions=adaptive_decisions,
            tools=tools,
            memory=memory,
            selected_model=selected_model,
        )

        # Registra que la planificación con LangChain fue completada o al menos intentada.
        memory.add_step(
            name="langchain_planning",
            status="completed",
            detail="LangChain AgentExecutor generated or attempted the orchestration plan.",
        )

        # Detiene el análisis si no se seleccionaron CV.
        # Aun así se devuelve una traza para que el frontend pueda mostrar qué ocurrió.
        if not request.cv_ids:
            memory.add_step(
                name="validate_candidates",
                status="failed",
                detail="No CV files were selected.",
            )
            memory.set_final_summary({"status": "failed", "reason": "No CV files were selected."})
            memory.save_long_term_memory()

            return AnalysisResponse(
                announcement_name=request.announcement_id,
                competencies=[],
                candidates=[],
                ranking=[],
                recommended_terna=[],
                report=None,
                progress_log=[],
                agent_trace=self._build_trace(
                    memory=memory,
                    plan=plan,
                    planning_output=planning_output,
                    tools=tools,
                    execution_mode="langchain_planned_controlled_execution",
                ),
            )

        # Lista local para guardar los mensajes de avance del análisis.
        progress_log: list[str] = []

        def emit(message: str) -> None:
            # Envía mensajes de progreso tanto al registro local como al callback del frontend.
            progress_log.append(message)
            if progress_callback:
                progress_callback({"message": message})

        emit("Agente LangChain: preparando anuncio laboral...")
        memory.add_step("prepare_announcement", "running", "Preparing announcement text.")

        # Extrae el texto del anuncio desde el archivo seleccionado o desde el texto ingresado manualmente.
        announcement_text = tools_builder.extract_announcement_text(
            announcement_id=request.announcement_id,
            announcement_text_override=request.announcement_text_override,
        )

        # El análisis no puede continuar si no existe texto legible del anuncio.
        if not announcement_text.strip():
            raise ValueError(
                "No se pudo extraer texto del anuncio. Instala Tesseract OCR o pega el texto del anuncio manualmente."
            )

        # Registra que el texto del anuncio fue preparado correctamente.
        memory.add_step(
            "prepare_announcement",
            "completed",
            f"Announcement text length: {len(announcement_text)} characters.",
        )

        emit("Agente LangChain: deduciendo competencias...")
        memory.add_step("extract_competencies", "running", "Extracting competencies from the announcement.")

        # Deduce las competencias requeridas a partir del texto del anuncio.
        # La respuesta JSON se transforma luego en objetos Competency validados.
        competencies_json = tools_builder.extract_competencies(announcement_text)
        competencies = [Competency(**item) for item in json.loads(competencies_json)]

        # Registra cuántas competencias fueron detectadas.
        memory.add_step(
            "extract_competencies",
            "completed",
            f"{len(competencies)} competencies extracted.",
        )

        # Lista donde se almacenan los resultados de evaluación de cada candidato.
        candidate_results: list[CandidateEvaluation] = []

        # Evalúa cada CV seleccionado de forma independiente.
        # Para cada candidato se extrae texto, se recupera evidencia y se compara contra las competencias.
        for index, cv_id in enumerate(request.cv_ids, start=1):
            emit(f"Agente LangChain: evaluando CV {index}/{len(request.cv_ids)}: {cv_id}")
            memory.add_step("evaluate_candidate", "running", f"Evaluating candidate file: {cv_id}.")

            # Extrae texto plano desde el CV del candidato.
            cv_text = tools_builder.extract_cv_text(cv_id)

            # Evalúa al candidato usando evidencia recuperada mediante RAG y el LLM seleccionado cuando está disponible.
            candidate_json = tools_builder.evaluate_candidate_with_rag(
                cv_id=cv_id,
                cv_text=cv_text,
                competencies_json=competencies_json,
            )

            # Convierte la respuesta JSON en un objeto CandidateEvaluation validado.
            candidate = CandidateEvaluation(**json.loads(candidate_json))
            candidate_results.append(candidate)

            # Registra el resultado resumido de la evaluación del candidato.
            memory.add_step(
                "evaluate_candidate",
                "completed",
                (
                    f"{cv_id} evaluated. "
                    f"Score: {candidate.normalized_score}/100. "
                    f"Recommendation: {candidate.recommendation}."
                ),
            )

        emit("Agente LangChain: generando ranking y terna...")
        memory.add_step("rank_candidates", "running", "Ranking candidates.")

        # Calcula el ranking final y la terna recomendada según los puntajes de los candidatos.
        ranking_json = tools_builder.rank_candidates(
            candidate_results_json=json.dumps(
                [candidate.model_dump(mode="json") for candidate in candidate_results],
                ensure_ascii=False,
            ),
            terna_size=request.terna_size,
        )

        # Convierte el JSON del ranking nuevamente en objetos validados de respuesta.
        ranking_data = json.loads(ranking_json)
        ranking = [CandidateEvaluation(**item) for item in ranking_data["ranking"]]
        terna = [CandidateEvaluation(**item) for item in ranking_data["terna"]]

        # Registra que el ranking fue generado correctamente.
        memory.add_step("rank_candidates", "completed", f"Ranking generated with {len(ranking)} candidates.")

        # Crea la respuesta principal del análisis antes de adjuntar el reporte generado.
        result = AnalysisResponse(
            announcement_name=request.announcement_id,
            competencies=competencies,
            candidates=candidate_results,
            ranking=ranking,
            recommended_terna=terna,
            report=None,
            progress_log=progress_log,
            agent_trace=None,
        )

        emit("Agente LangChain: generando reportes...")
        memory.add_step("write_report", "running", "Writing Markdown and JSON reports.")

        # Genera los reportes locales del análisis en formato Markdown y JSON.
        report_json = tools_builder.write_analysis_report(
            analysis_response_json=result.model_dump_json(),
            job_id=job_id,
            announcement_id=request.announcement_id,
        )

        # Adjunta el reporte generado a la respuesta principal.
        report = json.loads(report_json)
        result.report = report

        # Registra que los reportes fueron creados.
        memory.add_step("write_report", "completed", "Reports generated.")

        # Guarda un resumen compacto de la ejecución para trazabilidad posterior.
        final_summary = {
            "status": "completed",
            "announcement_id": request.announcement_id,
            "cv_count": len(request.cv_ids),
            "competencies_count": len(competencies),
            "ranking_count": len(ranking),
            "terna_count": len(terna),
            "top_candidate": ranking[0].candidate_name if ranking else None,
            "selected_model": selected_model,
            "report": report,
        }

        # Guarda la memoria del agente usando las herramientas disponibles.
        tools_builder.save_agent_memory(
            session_summary_json=json.dumps(final_summary, ensure_ascii=False)
        )

        # Registra que la memoria de largo plazo fue guardada.
        memory.add_step("save_memory", "completed", "Long-term memory saved.")

        # Adjunta la traza completa del agente a la respuesta.
        # Incluye plan, herramientas, salida de planificación y registros de memoria.
        result.agent_trace = self._build_trace(
            memory=memory,
            plan=plan,
            planning_output=planning_output,
            tools=tools,
            execution_mode="langchain_planned_controlled_execution",
        )

        emit("Agente LangChain: análisis finalizado.")

        return result

    def _run_langchain_planning(
        self,
        *,
        request: AnalysisRequest,
        plan: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        tools: list[Any],
        memory: AgentMemory,
        selected_model: str,
    ) -> str:
        # Si el LLM está desactivado o no hay credenciales, se omite la planificación con LangChain.
        # En ese caso se continúa usando el flujo determinístico controlado.
        if not self._is_llm_available():
            memory.add_decision(
                decision="skip_langchain_llm_planning",
                reason="GitHub Models credentials are not configured or USE_LLM is false.",
                outcome="The agent will use deterministic planning and controlled tool execution.",
            )
            return "LangChain LLM planning skipped because credentials are not configured."

        try:
            # Construye el LLM compatible con LangChain usando el mismo modelo seleccionado.
            llm = self._build_llm(selected_model)

            # Define el prompt de planificación.
            # En esta etapa el agente solo explica el orden de uso de herramientas, no evalúa candidatos.
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        (
                            "Eres un agente de preselección documental de candidatos. "
                            "Debes planificar el flujo usando herramientas de consulta, razonamiento, "
                            "escritura y memoria. No evalúes candidatos en esta etapa; solo explica "
                            "qué herramientas usarás y en qué orden."
                        ),
                    ),
                    (
                        "human",
                        (
                            "Solicitud recibida:\n"
                            "- Anuncio: {announcement_id}\n"
                            "- Cantidad de CV: {cv_count}\n"
                            "- Modelo seleccionado: {selected_model}\n"
                            "- Plan determinístico sugerido: {plan}\n"
                            "- Decisiones adaptativas iniciales: {decisions}\n\n"
                            "Genera una planificación breve y técnica del uso de herramientas."
                        ),
                    ),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                ]
            )

            # Crea un agente de LangChain basado en herramientas para la etapa de planificación.
            agent = create_openai_tools_agent(llm=llm, tools=tools, prompt=prompt)

            # Limita la ejecución del agente para mantener la planificación controlada.
            executor = AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=False,
                handle_parsing_errors=True,
                max_iterations=3,
            )

            # Ejecuta el agente con los datos principales de la solicitud.
            response = executor.invoke(
                {
                    "announcement_id": request.announcement_id,
                    "cv_count": len(request.cv_ids),
                    "selected_model": selected_model,
                    "plan": json.dumps(plan, ensure_ascii=False),
                    "decisions": json.dumps(decisions, ensure_ascii=False),
                }
            )

            # Obtiene la salida textual de planificación generada por LangChain.
            output = str(response.get("output", response))

            # Registra la llamada a la herramienta/agente para trazabilidad.
            memory.add_tool_call(
                tool_name="langchain_agent_executor",
                input_summary="planning request",
                output_summary=output[:500],
                success=True,
            )

            return output

        # Si falla la planificación con LangChain, el flujo controlado del backend continúa igualmente.
        except Exception as exc:
            memory.add_decision(
                decision="fallback_to_controlled_execution",
                reason=f"LangChain planning failed: {type(exc).__name__}.",
                outcome="The deterministic planner and controlled workflow will be used.",
                metadata={"error": str(exc)},
            )
            return f"LangChain planning failed and fallback was used: {exc}"

    def _build_llm(self, selected_model: str) -> ChatOpenAI:
        # Lee el token de GitHub Models desde las variables de entorno.
        token = os.getenv("GITHUB_TOKEN", "")

        # Lee el endpoint configurado para GitHub Models.
        endpoint = os.getenv(
            "GITHUB_MODELS_ENDPOINT",
            "https://models.github.ai/inference",
        ).strip()

        # LangChain ChatOpenAI expects the OpenAI-compatible base URL,
        # not the full /chat/completions endpoint.
        if endpoint.endswith("/chat/completions"):
            endpoint = endpoint.removesuffix("/chat/completions")

        # Devuelve un cliente compatible con ChatOpenAI apuntando a GitHub Models.
        return ChatOpenAI(
            model=selected_model,
            api_key=token,
            base_url=endpoint,
            temperature=0.1,
        )

    def _is_llm_available(self) -> bool:
        # Verifica si el uso del LLM online está habilitado en las variables de entorno.
        use_llm = os.getenv("USE_LLM", "true").lower() == "true"

        # Verifica si existe un token configurado para llamar al modelo.
        token = bool(os.getenv("GITHUB_TOKEN", "").strip())

        # El LLM solo se considera disponible si está habilitado y tiene token.
        return use_llm and token

    def _build_trace(
        self,
        *,
        memory: AgentMemory,
        plan: list[dict[str, Any]],
        planning_output: str,
        tools: list[Any],
        execution_mode: str,
    ) -> dict[str, Any]:
        # Construye la traza final que se muestra en la respuesta del análisis.
        # Esto permite auditar el flujo seguido por el agente.
        return {
            "framework": "LangChain",
            "agent_type": "openai_tools_agent",
            "execution_mode": execution_mode,
            "tools": [{"name": tool.name, "description": tool.description} for tool in tools],
            "plan": plan,
            "planning_output": planning_output,
            "memory": memory.to_trace(),
        }

    def _llm_status(self, client: GitHubModelsClient) -> dict[str, Any]:
        # Informa al frontend el estado actual de configuración del LLM.
        return {
            "provider": "GitHub Models",
            "use_llm": bool(client.enabled),
            "configured": bool(client.token),
            "model": client.model,
            "endpoint": client.endpoint,
            "mode": "github_models" if client.enabled else "local_fallback",
            "message": (
                "Modelo en línea habilitado."
                if client.enabled
                else "Modo local activo: no se usará GitHub Models para el análisis."
            ),
        }

    def _ethical_notes(self) -> list[str]:
        # Entrega recordatorios éticos para interpretar correctamente el ranking.
        return [
            "El ranking es una preselección documental y no reemplaza la decisión humana.",
            "El sistema debe ignorar edad, género, fotografía, nacionalidad, estado civil y datos familiares.",
            "Las recomendaciones se basan en evidencia textual presente en los CV y en el anuncio laboral.",
        ]