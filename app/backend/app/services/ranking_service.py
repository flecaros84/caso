from __future__ import annotations

import os
import re

from app.models.schemas import CandidateEvaluation, CompetencyEvaluation


class RankingService:
    """Builds candidate results and ranking using configurable thresholds."""

    def __init__(self) -> None:
        self.recommended_threshold = float(os.getenv("RECOMMENDED_THRESHOLD", "75"))
        self.considerable_threshold = float(os.getenv("CONSIDERABLE_THRESHOLD", "55"))

    def build_candidate_result(
        self,
        candidate_id: str,
        candidate_filename: str,
        evaluations: list[CompetencyEvaluation],
    ) -> CandidateEvaluation:
        total_score = sum(e.weighted_score for e in evaluations)
        normalized_score = min(round((total_score / 4.0) * 100, 2), 100.0)
        strengths = self._strengths(evaluations)
        gaps = self._gaps(evaluations)
        recommendation = self._recommendation(normalized_score)

        return CandidateEvaluation(
            candidate_id=candidate_id,
            candidate_name=self._clean_name(candidate_filename),
            total_score=round(total_score, 4),
            normalized_score=normalized_score,
            evaluations=evaluations,
            strengths=strengths,
            gaps=gaps,
            recommendation=recommendation,  # type: ignore[arg-type]
        )

    def rank(self, candidates: list[CandidateEvaluation]) -> list[CandidateEvaluation]:
        return sorted(candidates, key=lambda c: c.normalized_score, reverse=True)

    def _strengths(self, evaluations: list[CompetencyEvaluation]) -> list[str]:
        return [e.competency.name for e in evaluations if e.evidence_score >= 3][:5]

    def _gaps(self, evaluations: list[CompetencyEvaluation]) -> list[str]:
        return [e.competency.name for e in evaluations if e.evidence_score <= 1][:5]

    def _recommendation(self, score: float) -> str:
        if score >= self.recommended_threshold:
            return "recomendado"
        if score >= self.considerable_threshold:
            return "considerable"
        return "no_prioritario"

    def _clean_name(self, filename: str) -> str:
        name = re.sub(r"\.(pdf|txt|md|docx)$", "", filename, flags=re.IGNORECASE)
        return name.replace("CV", "").replace("cv", "").strip(" -_") or filename
