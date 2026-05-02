from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class ReportService:
    """Persists each analysis result as JSON and Markdown files."""

    def __init__(self, reports_dir: Path):
        self.reports_dir = reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def save_analysis_report(
        self,
        *,
        result: Any,
        job_id: str | None = None,
        announcement_id: str | None = None,
    ) -> dict[str, str]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_announcement = self._safe_name(announcement_id or "anuncio")
        safe_job = self._safe_name(job_id[:8] if job_id else "directo")
        base_name = f"reporte_{safe_announcement}_{timestamp}_{safe_job}"

        result_dict = self._to_dict(result)

        json_filename = f"{base_name}.json"
        markdown_filename = f"{base_name}.md"

        json_path = self.reports_dir / json_filename
        markdown_path = self.reports_dir / markdown_filename

        json_path.write_text(
            json.dumps(result_dict, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
        safe_filename = Path(filename).name
        path = self.reports_dir / safe_filename

        if not path.exists() or not path.is_file():
            raise FileNotFoundError("Reporte no encontrado.")

        if path.suffix.lower() not in {".json", ".md"}:
            raise ValueError("Tipo de reporte no permitido.")

        return path

    def list_reports(self) -> list[dict[str, str]]:
        reports = []

        for path in sorted(self.reports_dir.glob("reporte_*.*"), reverse=True):
            if path.suffix.lower() not in {".json", ".md"}:
                continue
            reports.append({
                "filename": path.name,
                "url": f"/api/reports/{path.name}",
                "size_bytes": str(path.stat().st_size),
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            })

        return reports

    def _to_dict(self, result: Any) -> dict[str, Any]:
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
        return json.loads(json.dumps(result, default=str))

    def _safe_name(self, value: str) -> str:
        value = str(value or "reporte").strip().lower()
        value = re.sub(r"[^a-z0-9_-]+", "_", value)
        value = re.sub(r"_+", "_", value).strip("_")
        return value or "reporte"

    def _build_markdown(self, result: dict[str, Any]) -> str:
        lines: list[str] = []
        lines.append("# Reporte de análisis de candidatos")
        lines.append("")
        lines.append(f"**Fecha de generación:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"**Anuncio analizado:** `{result.get('announcement_id', 'No informado')}`")
        lines.append("")

        llm_status = result.get("llm_status") or {}
        if llm_status:
            lines.append("## Estado del modelo IA")
            lines.append("")
            lines.append(f"- **Proveedor:** {llm_status.get('provider', 'No informado')}")
            lines.append(f"- **Modelo:** `{llm_status.get('model', 'No informado')}`")
            lines.append(f"- **Modo:** `{llm_status.get('mode', 'No informado')}`")
            lines.append(f"- **Endpoint:** `{llm_status.get('endpoint', 'No informado')}`")
            lines.append(f"- **Uso LLM habilitado:** {llm_status.get('use_llm', False)}")
            lines.append("")

        lines.append("## Competencias deducidas")
        lines.append("")
        lines.append("| Competencia | Categoría | Importancia | Peso | Evidencia esperada | Texto fuente |")
        lines.append("|---|---|---:|---:|---|---|")

        for comp in result.get("competencies", []):
            lines.append(
                "| "
                f"{self._md_cell(comp.get('name'))} | "
                f"{self._md_cell(comp.get('category'))} | "
                f"{self._md_cell(comp.get('importance'))} | "
                f"{round(float(comp.get('weight', 0)) * 100)}% | "
                f"{self._md_cell(comp.get('expected_evidence'))} | "
                f"{self._md_cell(comp.get('source_text') or comp.get('reason'))} |"
            )
        lines.append("")

        lines.append("## Terna recomendada")
        lines.append("")
        lines.append("| Lugar | Candidato | Puntaje | Recomendación | Fortalezas | Brechas |")
        lines.append("|---:|---|---:|---|---|---|")

        for index, candidate in enumerate(result.get("terna", []), start=1):
            lines.append(
                "| "
                f"{index} | "
                f"{self._md_cell(candidate.get('candidate_name'))} | "
                f"{candidate.get('normalized_score', 0)} | "
                f"{self._md_cell(candidate.get('recommendation'))} | "
                f"{self._md_cell(', '.join(candidate.get('strengths') or []))} | "
                f"{self._md_cell(', '.join(candidate.get('gaps') or []))} |"
            )
        lines.append("")

        lines.append("## Ranking completo")
        lines.append("")

        for index, candidate in enumerate(result.get("ranking", []), start=1):
            lines.append(f"### {index}. {candidate.get('candidate_name', 'Candidato sin nombre')}")
            lines.append("")
            lines.append(f"- **Puntaje:** {candidate.get('normalized_score', 0)}/100")
            lines.append(f"- **Recomendación:** {candidate.get('recommendation', 'No informada')}")
            lines.append("")
            lines.append("| Competencia | Nivel | Puntaje | Explicación | Evidencias |")
            lines.append("|---|---|---:|---|---|")

            for evaluation in candidate.get("evaluations", []):
                competency = evaluation.get("competency") or {}
                evidences = evaluation.get("evidences") or []
                evidence_text = " / ".join(e.get("text", "") for e in evidences[:2])
                lines.append(
                    "| "
                    f"{self._md_cell(competency.get('name'))} | "
                    f"{self._md_cell(evaluation.get('evidence_level'))} | "
                    f"{evaluation.get('evidence_score', 0)}/4 | "
                    f"{self._md_cell(evaluation.get('explanation'))} | "
                    f"{self._md_cell(evidence_text)} |"
                )
            lines.append("")

        ethical_notes = result.get("ethical_notes") or []
        if ethical_notes:
            lines.append("## Notas éticas y limitaciones")
            lines.append("")
            for note in ethical_notes:
                lines.append(f"- {note}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("Este reporte fue generado automáticamente por la aplicación de evaluación documental por competencias. La salida debe ser revisada por una persona responsable del proceso de selección.")
        lines.append("")

        return "\n".join(lines)

    def _md_cell(self, value: Any) -> str:
        text = str(value or "").replace("\n", " ").replace("\r", " ")
        text = re.sub(r"\s+", " ", text).strip()
        text = text.replace("|", "\\|")
        return text
