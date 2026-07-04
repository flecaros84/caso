from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class ReportService:
    """
    Servicio encargado de persistir los resultados de cada análisis.

    Genera dos salidas por ejecución:
    - un archivo JSON con el resultado técnico completo;
    - un archivo Markdown legible para revisión humana.

    En la versión EP3 también incorpora una sección de observabilidad,
    con métricas de rendimiento, uso de LLM/fallback, anomalías,
    recomendaciones y criterios de uso responsable.
    """

    def __init__(self, reports_dir: Path):
        """
        Inicializa el servicio de reportes.

        reports_dir:
            Carpeta donde se guardarán los reportes generados.
        """

        self.reports_dir = reports_dir

        # Creamos la carpeta de reportes si aún no existe.
        # parents=True permite crear carpetas intermedias.
        # exist_ok=True evita error si la carpeta ya estaba creada.
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def save_analysis_report(
        self,
        *,
        result: Any,
        job_id: str | None = None,
        announcement_id: str | None = None,
    ) -> dict[str, str]:
        """
        Guarda el resultado de un análisis en formato JSON y Markdown.

        result:
            Resultado final del análisis. Puede ser un modelo Pydantic
            o un diccionario normal.

        job_id:
            Identificador de ejecución. Se usa para diferenciar reportes.

        announcement_id:
            Identificador del anuncio analizado. Se usa en el nombre del archivo.
        """

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Sanitizamos el nombre del anuncio y del job para evitar caracteres
        # problemáticos en nombres de archivo.
        safe_announcement = self._safe_name(announcement_id or "anuncio")
        safe_job = self._safe_name(job_id[:8] if job_id else "directo")

        base_name = f"reporte_{safe_announcement}_{timestamp}_{safe_job}"

        # Convertimos el resultado a dict para poder guardarlo y procesarlo.
        result_dict = self._to_dict(result)

        json_filename = f"{base_name}.json"
        markdown_filename = f"{base_name}.md"

        json_path = self.reports_dir / json_filename
        markdown_path = self.reports_dir / markdown_filename

        # Guardamos el JSON completo. ensure_ascii=False conserva tildes
        # y caracteres propios del español.
        json_path.write_text(
            json.dumps(result_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Guardamos una versión Markdown orientada a lectura humana.
        markdown_path.write_text(
            self._build_markdown(result_dict),
            encoding="utf-8",
        )

        return {
            "json_filename": json_filename,
            "markdown_filename": markdown_filename,
            "json_url": f"/api/reports/{json_filename}",
            "markdown_url": f"/api/reports/{markdown_filename}",
            "directory": str(self.reports_dir),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def resolve_report(self, filename: str) -> Path:
        """
        Resuelve la ruta segura de un reporte solicitado desde la API.

        Se usa Path(filename).name para evitar traversal de rutas, por ejemplo:
        ../../archivo_sensible
        """

        safe_filename = Path(filename).name
        path = self.reports_dir / safe_filename

        if not path.exists() or not path.is_file():
            raise FileNotFoundError("Reporte no encontrado.")

        if path.suffix.lower() not in {".json", ".md"}:
            raise ValueError("Tipo de reporte no permitido.")

        return path

    def list_reports(self) -> list[dict[str, str]]:
        """
        Lista los reportes generados.

        Retorna metadatos simples para que el frontend o la API puedan
        mostrar enlaces a reportes anteriores.
        """

        reports: list[dict[str, str]] = []

        for path in sorted(self.reports_dir.glob("reporte_*.*"), reverse=True):
            if path.suffix.lower() not in {".json", ".md"}:
                continue

            reports.append(
                {
                    "filename": path.name,
                    "url": f"/api/reports/{path.name}",
                    "size_bytes": str(path.stat().st_size),
                    "modified_at": datetime.fromtimestamp(
                        path.stat().st_mtime
                    ).isoformat(timespec="seconds"),
                }
            )

        return reports

    def _to_dict(self, result: Any) -> dict[str, Any]:
        """
        Convierte el resultado a diccionario.

        Soporta:
        - modelos Pydantic v2 mediante model_dump;
        - diccionarios normales;
        - otros objetos serializables a JSON.
        """

        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")

        if isinstance(result, dict):
            return result

        return json.loads(json.dumps(result, default=str))

    def _safe_name(self, value: str) -> str:
        """
        Convierte un texto cualquiera en un nombre seguro para archivo.

        Ejemplo:
        "Anuncio 2 / Finanzas" -> "anuncio_2_finanzas"
        """

        value = str(value or "reporte").strip().lower()
        value = re.sub(r"[^a-z0-9_-]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")

        return value or "reporte"

    def _build_markdown(self, result: dict[str, Any]) -> str:
        """
        Construye el contenido Markdown del reporte.

        El reporte se organiza en secciones:
        - encabezado general;
        - estado del modelo IA;
        - competencias deducidas;
        - terna recomendada;
        - ranking completo;
        - observabilidad del agente;
        - orquestación/trazabilidad del agente;
        - notas éticas y limitaciones.
        """

        lines: list[str] = []

        lines.append("# Reporte de análisis de candidatos")
        lines.append("")
        lines.append(f"**Fecha de generación:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        announcement_value = (
            result.get("announcement_id")
            or result.get("announcement_name")
            or "No informado"
        )

        lines.append(f"**Anuncio analizado:** `{self._md_cell(announcement_value)}`")
        lines.append("")

        # ------------------------------------------------------------------
        # Estado del modelo IA
        # ------------------------------------------------------------------
        llm_status = result.get("llm_status") or {}

        if llm_status:
            lines.append("## Estado del modelo IA")
            lines.append("")
            lines.append(f"- **Proveedor:** {self._md_cell(llm_status.get('provider', 'No informado'))}")
            lines.append(f"- **Modelo:** `{self._md_cell(llm_status.get('model', 'No informado'))}`")
            lines.append(f"- **Modo:** `{self._md_cell(llm_status.get('mode', 'No informado'))}`")
            lines.append(f"- **Endpoint:** `{self._md_cell(llm_status.get('endpoint', 'No informado'))}`")
            lines.append(f"- **Uso LLM habilitado:** {llm_status.get('use_llm', False)}")
            lines.append("")

        # ------------------------------------------------------------------
        # Competencias deducidas desde el anuncio
        # ------------------------------------------------------------------
        lines.append("## Competencias deducidas")
        lines.append("")
        lines.append("| Competencia | Categoría | Importancia | Peso | Evidencia esperada | Texto fuente |")
        lines.append("|---|---|---:|---:|---|---|")

        for comp in result.get("competencies", []):
            weight_percent = self._safe_percent(comp.get("weight", 0))

            lines.append(
                "| "
                f"{self._md_cell(comp.get('name'))} | "
                f"{self._md_cell(comp.get('category'))} | "
                f"{self._md_cell(comp.get('importance'))} | "
                f"{weight_percent}% | "
                f"{self._md_cell(comp.get('expected_evidence'))} | "
                f"{self._md_cell(comp.get('source_text') or comp.get('reason'))} |"
            )

        lines.append("")

        # ------------------------------------------------------------------
        # Terna recomendada
        # ------------------------------------------------------------------
        lines.append("## Terna recomendada")
        lines.append("")
        lines.append("| Lugar | Candidato | Puntaje | Recomendación | Fortalezas | Brechas |")
        lines.append("|---:|---|---:|---|---|---|")

        recommended = result.get("terna") or result.get("recommended_terna") or []

        for index, candidate in enumerate(recommended, start=1):
            strengths = ", ".join(candidate.get("strengths") or [])
            gaps = ", ".join(candidate.get("gaps") or [])

            lines.append(
                "| "
                f"{index} | "
                f"{self._md_cell(candidate.get('candidate_name'))} | "
                f"{candidate.get('normalized_score', 0)} | "
                f"{self._md_cell(candidate.get('recommendation'))} | "
                f"{self._md_cell(strengths)} | "
                f"{self._md_cell(gaps)} |"
            )

        lines.append("")

        # ------------------------------------------------------------------
        # Ranking completo con detalle por competencia
        # ------------------------------------------------------------------
        lines.append("## Ranking completo")
        lines.append("")

        for index, candidate in enumerate(result.get("ranking", []), start=1):
            candidate_name = candidate.get("candidate_name", "Candidato sin nombre")

            lines.append(f"### {index}. {self._md_cell(candidate_name)}")
            lines.append("")
            lines.append(f"- **Puntaje:** {candidate.get('normalized_score', 0)}/100")
            lines.append(f"- **Recomendación:** {self._md_cell(candidate.get('recommendation', 'No informada'))}")
            lines.append("")
            lines.append("| Competencia | Nivel | Puntaje | Explicación | Evidencias |")
            lines.append("|---|---|---:|---|---|")

            for evaluation in candidate.get("evaluations", []):
                competency = evaluation.get("competency") or {}
                evidences = evaluation.get("evidences") or []

                # Para no hacer una tabla excesivamente larga, se muestran
                # solo las dos primeras evidencias recuperadas por RAG.
                evidence_text = " / ".join(
                    evidence.get("text", "")
                    for evidence in evidences[:2]
                )

                lines.append(
                    "| "
                    f"{self._md_cell(competency.get('name'))} | "
                    f"{self._md_cell(evaluation.get('evidence_level'))} | "
                    f"{evaluation.get('evidence_score', 0)}/4 | "
                    f"{self._md_cell(evaluation.get('explanation'))} | "
                    f"{self._md_cell(evidence_text)} |"
                )

            lines.append("")

        # ------------------------------------------------------------------
        # Observabilidad EP3
        # ------------------------------------------------------------------
        # Esta sección es clave para la pauta: deja evidencia directa de
        # métricas, logs, anomalías, recomendaciones y uso responsable.
        lines.extend(self._build_observability_section(result))

        # ------------------------------------------------------------------
        # Trazabilidad/orquestación del agente
        # ------------------------------------------------------------------
        agent_trace = result.get("agent_trace") or {}

        if agent_trace:
            lines.append("## Orquestación del agente LangChain")
            lines.append("")
            lines.append(f"- **Framework:** {self._md_cell(agent_trace.get('framework'))}")
            lines.append(f"- **Tipo de agente:** `{self._md_cell(agent_trace.get('agent_type'))}`")
            lines.append(f"- **Modo de ejecución:** `{self._md_cell(agent_trace.get('execution_mode'))}`")
            lines.append("")

            tools = agent_trace.get("tools") or []

            if tools:
                lines.append("### Herramientas declaradas")
                lines.append("")
                lines.append("| Herramienta | Descripción |")
                lines.append("|---|---|")

                for tool in tools:
                    lines.append(
                        "| "
                        f"`{self._md_cell(tool.get('name'))}` | "
                        f"{self._md_cell(tool.get('description'))} |"
                    )

                lines.append("")

            plan = agent_trace.get("plan") or []

            if plan:
                lines.append("### Plan de ejecución")
                lines.append("")
                lines.append("| Orden | Paso | Tipo | Herramienta | Descripción |")
                lines.append("|---:|---|---|---|---|")

                for step in plan:
                    lines.append(
                        "| "
                        f"{step.get('order', '')} | "
                        f"{self._md_cell(step.get('name'))} | "
                        f"{self._md_cell(step.get('step_type'))} | "
                        f"`{self._md_cell(step.get('tool_name'))}` | "
                        f"{self._md_cell(step.get('description'))} |"
                    )

                lines.append("")

            planning_output = agent_trace.get("planning_output")

            if planning_output:
                lines.append("### Planificación generada por LangChain")
                lines.append("")
                lines.append("> " + self._md_cell(planning_output[:1500]))
                lines.append("")

            memory = agent_trace.get("memory") or {}
            short_memory = memory.get("short_term_memory") or {}

            decisions = short_memory.get("decisions") or []

            if decisions:
                lines.append("### Decisiones adaptativas registradas")
                lines.append("")
                lines.append("| Decisión | Razón | Resultado |")
                lines.append("|---|---|---|")

                for decision in decisions:
                    lines.append(
                        "| "
                        f"{self._md_cell(decision.get('decision'))} | "
                        f"{self._md_cell(decision.get('reason'))} | "
                        f"{self._md_cell(decision.get('outcome'))} |"
                    )

                lines.append("")

            tool_calls = short_memory.get("tool_calls") or []

            if tool_calls:
                lines.append("### Trazabilidad de herramientas ejecutadas")
                lines.append("")
                lines.append("| Herramienta | Entrada | Salida | Éxito |")
                lines.append("|---|---|---|---:|")

                for call in tool_calls:
                    lines.append(
                        "| "
                        f"`{self._md_cell(call.get('tool_name'))}` | "
                        f"{self._md_cell(call.get('input_summary'))} | "
                        f"{self._md_cell(call.get('output_summary'))} | "
                        f"{call.get('success', False)} |"
                    )

                lines.append("")

            long_term_path = memory.get("long_term_memory_path")

            if long_term_path:
                lines.append("### Memoria de largo plazo")
                lines.append("")
                lines.append(f"- **Archivo de memoria:** `{self._md_cell(long_term_path)}`")
                lines.append("")

        # ------------------------------------------------------------------
        # Notas éticas y cierre
        # ------------------------------------------------------------------
        ethical_notes = result.get("ethical_notes") or []

        if ethical_notes:
            lines.append("## Notas éticas y limitaciones")
            lines.append("")

            for note in ethical_notes:
                lines.append(f"- {self._md_cell(note)}")

            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append(
            "Este reporte fue generado automáticamente por la aplicación de evaluación documental "
            "por competencias. La salida debe ser revisada por una persona responsable del proceso "
            "de selección."
        )
        lines.append("")

        return "\n".join(lines)

    def _build_observability_section(self, result: dict[str, Any]) -> list[str]:
        """
        Construye la sección Markdown de observabilidad.

        Esta sección resume el comportamiento observado durante la ejecución.
        Es especialmente útil para la EP3 porque conecta datos reales del agente
        con hallazgos, anomalías y recomendaciones de mejora.
        """

        observability = result.get("observability") or {}

        if not observability:
            return [
                "",
                "## Observabilidad del agente",
                "",
                "No se registraron métricas de observabilidad para esta ejecución.",
                "",
            ]

        dataset = observability.get("dataset") or {}
        llm = observability.get("llm") or {}
        performance = observability.get("performance") or {}
        quality = observability.get("quality") or {}
        ranking = observability.get("ranking") or {}
        anomalies = observability.get("anomalies") or []
        recommendations = observability.get("recommendations") or []
        responsible_ai = observability.get("responsible_ai") or {}

        lines: list[str] = [
            "",
            "## Observabilidad del agente",
            "",
            "Esta sección resume las métricas registradas durante la ejecución del análisis. "
            "Permite revisar el comportamiento del agente, identificar cuellos de botella "
            "y respaldar recomendaciones de mejora con datos observados.",
            "",
            "### Resumen de ejecución",
            "",
            f"- **Trace ID:** `{self._md_cell(observability.get('trace_id', 'No informado'))}`",
            f"- **Estado de ejecución:** `{self._md_cell(observability.get('status', 'No informado'))}`",
            f"- **Anuncio analizado:** `{self._md_cell(dataset.get('announcement_name', 'No informado'))}`",
            f"- **Candidatos evaluados:** {dataset.get('candidate_count', 0)}",
            f"- **Competencias evaluadas:** {dataset.get('competency_count', 0)}",
            f"- **Evaluaciones realizadas:** {dataset.get('evaluation_count', 0)}",
            "",
            "### Métricas de rendimiento",
            "",
            "| Métrica | Valor |",
            "|---|---:|",
            f"| Latencia total | {performance.get('total_latency_seconds', 0)} s |",
            f"| Latencia promedio por candidato | {performance.get('average_latency_per_candidate_seconds', 0)} s |",
            f"| Latencia promedio por evaluación | {performance.get('average_latency_per_evaluation_seconds', 0)} s |",
            "",
            "### Uso de LLM y fallback local",
            "",
            "| Métrica | Valor |",
            "|---|---:|",
            f"| Llamadas LLM exitosas | {llm.get('success_count', 0)} |",
            f"| Usos de fallback local | {llm.get('fallback_count', 0)} |",
            f"| Errores registrados | {llm.get('error_count', 0)} |",
            f"| Tasa de éxito LLM | {llm.get('success_rate', 0)}% |",
            f"| Tasa de fallback | {llm.get('fallback_rate', 0)}% |",
            f"| Tasa de error | {llm.get('error_rate', 0)}% |",
            "",
            "### Calidad de evidencia y ranking",
            "",
            "| Métrica | Valor |",
            "|---|---:|",
            f"| Evidencia promedio | {quality.get('average_evidence_score', 0)}/4 |",
            f"| Evidencia débil o no evidenciada | {quality.get('weak_evidence_rate', 0)}% |",
            f"| Evidencia clara o fuerte | {quality.get('strong_evidence_rate', 0)}% |",
            f"| Puntaje promedio de candidatos | {ranking.get('average_score', 0)}/100 |",
            f"| Puntaje máximo | {ranking.get('max_score', 0)}/100 |",
            f"| Puntaje mínimo | {ranking.get('min_score', 0)}/100 |",
            "",
            "### Anomalías detectadas",
            "",
        ]

        if anomalies:
            for anomaly in anomalies:
                severity = self._md_cell(
                    anomaly.get("severity", "sin severidad")
                ).capitalize()

                message = self._md_cell(anomaly.get("message", ""))

                lines.append(f"- **{severity}:** {message}")
        else:
            lines.append("- No se detectaron anomalías críticas en esta ejecución.")

        lines.extend(
            [
                "",
                "### Recomendaciones de mejora",
                "",
            ]
        )

        if recommendations:
            for recommendation in recommendations:
                lines.append(f"- {self._md_cell(recommendation)}")
        else:
            lines.append("- No se generaron recomendaciones automáticas.")

        lines.extend(
            [
                "",
                "### Seguridad, privacidad y uso responsable",
                "",
                f"- **Alcance de decisión:** `{self._md_cell(responsible_ai.get('decision_scope', 'apoyo_documental'))}`",
                f"- **Requiere revisión humana:** {responsible_ai.get('human_decision_required', True)}",
                "- El sistema debe utilizarse como apoyo documental y no como mecanismo automático de contratación.",
                "- La evaluación debe basarse en formación, experiencia, conocimientos técnicos, certificaciones y evidencia relacionada con el cargo.",
                "- No deben utilizarse variables sensibles como edad, género, nacionalidad, estado civil, fotografía, religión, salud u opiniones políticas.",
                "",
            ]
        )

        return lines

    def _safe_percent(self, value: Any) -> int:
        """
        Convierte un peso decimal a porcentaje entero.

        Ejemplo:
        0.25 -> 25

        Si el valor viene vacío o mal formado, retorna 0.
        """

        try:
            return round(float(value or 0) * 100)
        except (TypeError, ValueError):
            return 0

    def _md_cell(self, value: Any) -> str:
        """
        Limpia texto para usarlo dentro de Markdown.

        - Conserva valores válidos como 0, 0.0 y False.
        - Elimina saltos de línea.
        - Reduce espacios repetidos.
        - Escapa el carácter | para que no rompa tablas Markdown.
        """

        if value is None:
            text = ""
        else:
            text = str(value)

        text = text.replace("\n", " ").replace("\r", " ")
        text = re.sub(r"\s+", " ", text).strip()
        text = text.replace("|", "\\|")

        return text