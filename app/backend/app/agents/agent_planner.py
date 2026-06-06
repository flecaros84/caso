# app/backend/app/agents/agent_planner.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


# Representa un paso planificado dentro del flujo del agente.
# Se usa como estructura simple para ordenar tareas, herramientas y tipos de acción.
@dataclass
class AgentPlanStep:
    """
    Represents a planned step in the recruitment agent workflow.
    """

    # Orden numérico del paso dentro del flujo.
    order: int

    # Nombre interno del paso.
    name: str

    # Descripción breve de lo que realiza el paso.
    description: str

    # Nombre de la herramienta asociada al paso, si corresponde.
    tool_name: str | None = None

    # Tipo de paso: consulta, razonamiento, escritura, memoria u otro.
    step_type: str = "workflow"


class RecruitmentAgentPlanner:
    """
    Builds an explicit execution plan for the recruitment screening agent.

    The planner keeps the workflow simple and auditable:
    - It defines the order of tasks.
    - It identifies which tools should be used.
    - It registers adaptive decisions based on the request conditions.
    """

    def build_plan(
        self,
        announcement_name: str | None,
        manual_announcement_text: str | None,
        cv_names: list[str],
    ) -> list[dict[str, Any]]:
        # Define el plan base del agente.
        # Este plan mantiene un flujo fijo y auditable para el análisis de candidatos.
        plan = [
            AgentPlanStep(
                order=1,
                name="prepare_announcement",
                description="Prepare the job announcement text from manual input or file extraction.",
                tool_name="extract_announcement_text",
                step_type="consultation",
            ),
            AgentPlanStep(
                order=2,
                name="extract_competencies",
                description="Infer required competencies from the job announcement.",
                tool_name="extract_competencies",
                step_type="reasoning",
            ),
            AgentPlanStep(
                order=3,
                name="evaluate_candidates",
                description="Extract CV text, retrieve evidence with RAG, and evaluate each candidate.",
                tool_name="evaluate_candidate_with_rag",
                step_type="consultation_reasoning",
            ),
            AgentPlanStep(
                order=4,
                name="rank_candidates",
                description="Calculate candidate scores and generate the recommended shortlist.",
                tool_name="rank_candidates",
                step_type="reasoning",
            ),
            AgentPlanStep(
                order=5,
                name="write_report",
                description="Write Markdown and JSON reports with the final result.",
                tool_name="write_analysis_report",
                step_type="writing",
            ),
            AgentPlanStep(
                order=6,
                name="save_memory",
                description="Persist a summary of the agent execution in long-term memory.",
                tool_name="save_agent_memory",
                step_type="memory",
            ),
        ]

        # Convierte cada paso dataclass en diccionario.
        # Esto facilita guardar el plan en trazas, reportes o respuestas JSON.
        return [asdict(step) for step in plan]

    def build_adaptive_decisions(
        self,
        announcement_name: str | None,
        manual_announcement_text: str | None,
        cv_names: list[str],
    ) -> list[dict[str, Any]]:
        # Lista donde se guardan decisiones tomadas según las condiciones de la solicitud.
        decisions: list[dict[str, Any]] = []

        # Decide si se usará texto manual del anuncio o si se extraerá desde archivo.
        if manual_announcement_text and manual_announcement_text.strip():
            decisions.append(
                {
                    "decision": "use_manual_announcement_text",
                    "reason": "The request includes manual announcement text.",
                    "outcome": "The agent will use the manual text and skip OCR/file extraction for the announcement.",
                }
            )
        else:
            decisions.append(
                {
                    "decision": "extract_announcement_from_file",
                    "reason": "The request does not include manual announcement text.",
                    "outcome": "The agent will extract the announcement text from the selected file.",
                }
            )

        # Decide cómo actuar según la cantidad de CV seleccionados.
        # Si no hay CV, el agente no puede continuar con la evaluación.
        if not cv_names:
            decisions.append(
                {
                    "decision": "stop_without_candidates",
                    "reason": "No CV files were selected.",
                    "outcome": "The agent cannot evaluate candidates without CV files.",
                }
            )
        elif len(cv_names) < 3:
            decisions.append(
                {
                    "decision": "generate_partial_shortlist",
                    "reason": "Fewer than three CV files were selected.",
                    "outcome": "The agent will generate a partial shortlist instead of a complete three-candidate shortlist.",
                }
            )
        else:
            decisions.append(
                {
                    "decision": "generate_full_shortlist",
                    "reason": "Three or more CV files were selected.",
                    "outcome": "The agent will generate a full recommended shortlist.",
                }
            )

        # Si existe un nombre de anuncio, se registra como fuente documental del análisis.
        if announcement_name:
            decisions.append(
                {
                    "decision": "track_announcement_source",
                    "reason": "An announcement file name was provided.",
                    "outcome": f"The agent will register '{announcement_name}' as the announcement source.",
                }
            )

        # Devuelve todas las decisiones adaptativas tomadas para esta solicitud.
        return decisions