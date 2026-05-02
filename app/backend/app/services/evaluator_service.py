from __future__ import annotations

from typing import Any, Callable

from app.models.schemas import Competency, CompetencyEvaluation, Evidence
from app.services.llm_client import GitHubModelsClient
from app.services.rag_service import SimpleRAGIndex


class EvaluatorService:
    """
    Evaluates each candidate against dynamically extracted competencies.

    Flow per competency:
    1. Retrieve CV evidence with RAG.
    2. If LLM is enabled, ask the model to score only the retrieved evidence.
    3. If LLM is disabled or fails, use conservative similarity thresholds.
    """

    VALID_LEVELS = {"no_evidenciado", "debil", "parcial", "claro", "fuerte"}

    def __init__(self, llm: GitHubModelsClient | None = None) -> None:
        self.llm = llm or GitHubModelsClient()

    def evaluate_candidate(
        self,
        index: SimpleRAGIndex,
        competencies: list[Competency],
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[CompetencyEvaluation]:
        evaluations: list[CompetencyEvaluation] = []

        for idx, comp in enumerate(competencies, start=1):
            query = self._build_query(comp)
            results = index.search(query, top_k=5)
            evidences = [
                Evidence(text=r.text[:900], source=r.source, score=round(r.score, 4))
                for r in results[:4]
            ]

            llm_eval = self._evaluate_with_llm(comp, evidences)
            if llm_eval:
                evidence_score = llm_eval["evidence_score"]
                level = llm_eval["evidence_level"]
                explanation = llm_eval["explanation"]
            else:
                best_score = max([r.score for r in results], default=0.0)
                evidence_score, level = self._map_similarity_score(best_score)
                explanation = self._fallback_explanation(comp, level, evidences)

            weighted = evidence_score * comp.weight
            evaluations.append(CompetencyEvaluation(
                competency=comp,
                evidence_level=level,  # type: ignore[arg-type]
                evidence_score=round(evidence_score, 2),
                weighted_score=round(weighted, 4),
                evidences=evidences[:3],
                explanation=explanation,
            ))

            if progress_callback:
                progress_callback({
                    "candidate_id": index.candidate_id,
                    "candidate_name": index.candidate_name,
                    "competency_index": idx,
                    "competency_total": len(competencies),
                    "competency_name": comp.name,
                    "llm_success": bool(llm_eval),
                    "used_fallback": not bool(llm_eval),
                    "evidence_level": level,
                })

        return evaluations

    def _build_query(self, comp: Competency) -> str:
        source = comp.source_text or ""
        return f"{comp.name}. {comp.expected_evidence}. {source}"

    def _evaluate_with_llm(self, comp: Competency, evidences: list[Evidence]) -> dict[str, Any] | None:
        if not evidences:
            return None

        evidence_text = "\n\n".join(
            f"Evidencia {idx + 1} (similitud {ev.score}): {ev.text}"
            for idx, ev in enumerate(evidences)
        )[:6000]

        system = """
Eres un evaluador de selección por competencias basado estrictamente en evidencia documental.
Evalúa solo la evidencia del CV entregada. No inventes información.
Si la evidencia no respalda la competencia, marca no_evidenciado.
Ignora edad, género, nacionalidad, fotografía, estado civil, domicilio, familia u otras variables sensibles.
Responde solamente JSON válido.
""".strip()

        user = f"""
Competencia requerida:
- Nombre: {comp.name}
- Categoría: {comp.category}
- Importancia: {comp.importance}
- Evidencia esperada: {comp.expected_evidence}
- Texto fuente del anuncio: {comp.source_text or "No informado"}

Evidencia recuperada desde el CV:
--- INICIO EVIDENCIA ---
{evidence_text}
--- FIN EVIDENCIA ---

Evalúa el nivel de evidencia con esta escala:
0 = no_evidenciado: no hay evidencia útil.
1 = debil: la evidencia es muy indirecta o insuficiente.
2 = parcial: hay evidencia relacionada, pero incompleta.
3 = claro: hay evidencia suficiente y directa.
4 = fuerte: hay evidencia directa, específica y robusta.

Devuelve exactamente este JSON:
{{
  "evidence_score": 0,
  "evidence_level": "no_evidenciado",
  "explanation": "Explicación breve basada solo en la evidencia entregada."
}}
""".strip()

        data = self.llm.complete_json(system, user, max_tokens=900)
        if not isinstance(data, dict):
            return None

        try:
            score = float(data.get("evidence_score", 0))
        except (TypeError, ValueError):
            score = 0.0

        score = max(0.0, min(score, 4.0))
        level = str(data.get("evidence_level", "")).strip().lower()
        if level not in self.VALID_LEVELS:
            level = self._level_from_score(score)

        explanation = str(data.get("explanation", "")).strip()
        if not explanation:
            explanation = self._fallback_explanation(comp, level, evidences)

        return {
            "evidence_score": score,
            "evidence_level": level,
            "explanation": explanation,
        }

    def _map_similarity_score(self, score: float) -> tuple[float, str]:
        # Conservative fallback. The LLM path is preferred for nuanced interpretation.
        if score >= 0.55:
            return 4.0, "fuerte"
        if score >= 0.38:
            return 3.0, "claro"
        if score >= 0.22:
            return 2.0, "parcial"
        if score >= 0.10:
            return 1.0, "debil"
        return 0.0, "no_evidenciado"

    def _level_from_score(self, score: float) -> str:
        if score >= 3.5:
            return "fuerte"
        if score >= 2.5:
            return "claro"
        if score >= 1.5:
            return "parcial"
        if score >= 0.5:
            return "debil"
        return "no_evidenciado"

    def _fallback_explanation(self, comp: Competency, level: str, evidences: list[Evidence]) -> str:
        if level == "no_evidenciado" or not evidences:
            return f"No se encontró evidencia documental suficiente para la competencia '{comp.name}'."
        return f"Se encontró evidencia {level.replace('_', ' ')} para '{comp.name}' mediante recuperación semántica en el CV."
