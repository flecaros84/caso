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
from app.agents.token_usage_callback import LangChainTokenUsageCallback


class LangChainRecruitmentAgent:
    """
    LangChain-based recruitment screening agent.

    The agent uses LangChain for:
    - tool declaration,
    - planning,
    - orchestration traceability,
    - short-term and long-term memory.

    The business execution remains controlled by the backend to keep the
    candidate evaluation stable, auditable and reproducible.
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
        load_dotenv()

        self.file_service = file_service
        self.text_extractor = text_extractor
        self.ranking_service = ranking_service
        self.report_service = report_service
        self.model_catalog_service = model_catalog_service
        self.planner = RecruitmentAgentPlanner()

    def analyze(
        self,
        request: AnalysisRequest,
        *,
        job_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> AnalysisResponse:
        memory = AgentMemory()

        raw_selected_model = (request.selected_model or "").strip()

        if not raw_selected_model or raw_selected_model.lower() == "string":
            selected_model = self.model_catalog_service.get_default_model()
        else:
            selected_model = raw_selected_model

        if not self.model_catalog_service.is_allowed_model(selected_model):
            raise ValueError(
                f"Modelo no permitido o no registrado en github_models.json: {selected_model}"
            )

        llm_client = GitHubModelsClient(model=selected_model)
        competency_service = CompetencyService(llm=llm_client)
        evaluator_service = EvaluatorService(llm=llm_client)

        plan = self.planner.build_plan(
            announcement_name=request.announcement_id,
            manual_announcement_text=request.announcement_text_override,
            cv_names=request.cv_ids,
        )

        adaptive_decisions = self.planner.build_adaptive_decisions(
            announcement_name=request.announcement_id,
            manual_announcement_text=request.announcement_text_override,
            cv_names=request.cv_ids,
        )

        for decision in adaptive_decisions:
            memory.add_decision(
                decision=decision["decision"],
                reason=decision["reason"],
                outcome=decision["outcome"],
            )

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

        tools = tools_builder.build()

        planning_output = self._run_langchain_planning(
            request=request,
            plan=plan,
            decisions=adaptive_decisions,
            tools=tools,
            memory=memory,
            selected_model=selected_model,
            usage_recorder=llm_client,
        )

        memory.add_step(
            name="langchain_planning",
            status="completed",
            detail="LangChain AgentExecutor generated or attempted the orchestration plan.",
        )

        if not request.cv_ids:
            memory.add_step(
                name="validate_candidates",
                status="failed",
                detail="No CV files were selected.",
            )

            memory.set_final_summary(
                {
                    "status": "failed",
                    "reason": "No CV files were selected.",
                    "announcement_id": request.announcement_id,
                    "selected_model": selected_model,
                }
            )
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

        progress_log: list[str] = []

        def emit(message: str) -> None:
            progress_log.append(message)
            if progress_callback:
                progress_callback({"message": message})

        emit("Agente LangChain: preparando anuncio laboral...")
        memory.add_step(
            name="prepare_announcement",
            status="running",
            detail="Preparing announcement text.",
        )

        announcement_text = tools_builder.extract_announcement_text(
            announcement_id=request.announcement_id,
            announcement_text_override=request.announcement_text_override,
        )

        if not announcement_text.strip():
            raise ValueError(
                "No se pudo extraer texto del anuncio. Instala Tesseract OCR o pega el texto del anuncio manualmente."
            )

        memory.add_step(
            name="prepare_announcement",
            status="completed",
            detail=f"Announcement text length: {len(announcement_text)} characters.",
        )

        emit("Agente LangChain: deduciendo competencias...")
        memory.add_step(
            name="extract_competencies",
            status="running",
            detail="Extracting competencies from the announcement.",
        )

        competencies_json = tools_builder.extract_competencies(announcement_text)
        competencies = [Competency(**item) for item in json.loads(competencies_json)]

        memory.add_step(
            name="extract_competencies",
            status="completed",
            detail=f"{len(competencies)} competencies extracted.",
        )

        candidate_results: list[CandidateEvaluation] = []

        for index, cv_id in enumerate(request.cv_ids, start=1):
            emit(f"Agente LangChain: evaluando CV {index}/{len(request.cv_ids)}: {cv_id}")

            memory.add_step(
                name="evaluate_candidate",
                status="running",
                detail=f"Evaluating candidate file: {cv_id}.",
            )

            cv_text = tools_builder.extract_cv_text(cv_id)

            candidate_json = tools_builder.evaluate_candidate_with_rag(
                cv_id=cv_id,
                cv_text=cv_text,
                competencies_json=competencies_json,
            )

            candidate = CandidateEvaluation(**json.loads(candidate_json))
            candidate_results.append(candidate)

            memory.add_step(
                name="evaluate_candidate",
                status="completed",
                detail=(
                    f"{cv_id} evaluated. "
                    f"Score: {candidate.normalized_score}/100. "
                    f"Recommendation: {candidate.recommendation}."
                ),
            )

        emit("Agente LangChain: generando ranking y terna...")
        memory.add_step(
            name="rank_candidates",
            status="running",
            detail="Ranking candidates.",
        )

        ranking_json = tools_builder.rank_candidates(
            candidate_results_json=json.dumps(
                [candidate.model_dump(mode="json") for candidate in candidate_results],
                ensure_ascii=False,
            ),
            terna_size=request.terna_size,
        )

        ranking_data = json.loads(ranking_json)
        ranking = [CandidateEvaluation(**item) for item in ranking_data["ranking"]]
        terna = [CandidateEvaluation(**item) for item in ranking_data["terna"]]

        memory.add_step(
            name="rank_candidates",
            status="completed",
            detail=f"Ranking generated with {len(ranking)} candidates.",
        )

        result = AnalysisResponse(
            announcement_name=request.announcement_id,
            competencies=competencies,
            candidates=candidate_results,
            ranking=ranking,
            recommended_terna=terna,
            report=None,
            progress_log=progress_log,
            agent_trace=None,

            # Consumo de las llamadas de extracción y evaluación del agente.
            # La planificación LangChain se contabilizará por separado.
            llm_usage=llm_client.get_usage_summary(),
        )

        # Build a preliminary trace before report generation so the Markdown report
        # can include the agent orchestration section.
        result.agent_trace = self._build_trace(
            memory=memory,
            plan=plan,
            planning_output=planning_output,
            tools=tools,
            execution_mode="langchain_planned_controlled_execution",
        )

        emit("Agente LangChain: generando reportes...")
        memory.add_step(
            name="write_report",
            status="running",
            detail="Writing Markdown and JSON reports.",
        )

        report_json = tools_builder.write_analysis_report(
            analysis_response_json=result.model_dump_json(),
            job_id=job_id,
            announcement_id=request.announcement_id,
        )

        report = json.loads(report_json)
        result.report = report

        memory.add_step(
            name="write_report",
            status="completed",
            detail="Reports generated.",
        )

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

        tools_builder.save_agent_memory(
            session_summary_json=json.dumps(final_summary, ensure_ascii=False)
        )

        memory.add_step(
            name="save_memory",
            status="completed",
            detail="Long-term memory saved.",
        )

        # Build the final trace after report generation and memory persistence.
        result.agent_trace = self._build_trace(
            memory=memory,
            plan=plan,
            planning_output=planning_output,
            tools=tools,
            execution_mode="langchain_planned_controlled_execution",
        )

        emit("Agente LangChain: análisis finalizado.")
        result.progress_log = progress_log

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
        usage_recorder: GitHubModelsClient,
    ) -> str:
        if not self._is_llm_available():
            memory.add_decision(
                decision="skip_langchain_llm_planning",
                reason="GitHub Models credentials are not configured or USE_LLM is false.",
                outcome="The agent will use deterministic planning and controlled tool execution.",
            )
            return "LangChain LLM planning skipped because credentials are not configured."

        try:
            llm = self._build_llm(selected_model)

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

            agent = create_openai_tools_agent(
                llm=llm,
                tools=tools,
                prompt=prompt,
            )

            executor = AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=False,
                handle_parsing_errors=True,
                max_iterations=3,
            )

            token_usage_callback = LangChainTokenUsageCallback(
                usage_recorder
            )

            response = executor.invoke(
                {
                    "announcement_id": request.announcement_id,
                    "cv_count": len(request.cv_ids),
                    "selected_model": selected_model,
                    "plan": json.dumps(plan, ensure_ascii=False),
                    "decisions": json.dumps(
                        decisions,
                        ensure_ascii=False,
                    ),
                },
                config={
                    "callbacks": [token_usage_callback],
                },
            )

            output = str(response.get("output", response))

            memory.add_tool_call(
                tool_name="langchain_agent_executor",
                input_summary="planning request",
                output_summary=output[:500],
                success=True,
            )

            return output

        except Exception as exc:
            memory.add_decision(
                decision="fallback_to_controlled_execution",
                reason=f"LangChain planning failed: {type(exc).__name__}.",
                outcome="The deterministic planner and controlled workflow will be used.",
                metadata={"error": str(exc)},
            )
            return f"LangChain planning failed and fallback was used: {exc}"

    def _build_llm(self, selected_model: str) -> ChatOpenAI:
        token = os.getenv("GITHUB_TOKEN", "")

        endpoint = os.getenv(
            "GITHUB_MODELS_ENDPOINT",
            "https://models.github.ai/inference",
        ).strip()

        # LangChain ChatOpenAI expects the OpenAI-compatible base URL,
        # not the full /chat/completions endpoint.
        if endpoint.endswith("/chat/completions"):
            endpoint = endpoint.removesuffix("/chat/completions")

        return ChatOpenAI(
            model=selected_model,
            api_key=token,
            base_url=endpoint,
            temperature=0.1,
        )

    def _is_llm_available(self) -> bool:
        use_llm = os.getenv("USE_LLM", "true").lower() == "true"
        token = bool(os.getenv("GITHUB_TOKEN", "").strip())
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
        return {
            "framework": "LangChain",
            "agent_type": "openai_tools_agent",
            "execution_mode": execution_mode,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                }
                for tool in tools
            ],
            "plan": plan,
            "planning_output": planning_output,
            "memory": memory.to_trace(),
        }