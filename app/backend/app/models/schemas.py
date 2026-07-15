from typing import Any, Literal
from pydantic import BaseModel, Field


class FileItem(BaseModel):
    id: str
    filename: str
    path: str
    kind: Literal["announcement", "cv"]


class Competency(BaseModel):
    name: str
    category: Literal[
        "tecnica",
        "experiencia",
        "formacion",
        "transversal",
        "contextual",
        "requisito_formal",
    ] = "tecnica"
    weight: float = Field(ge=0, le=1)
    importance: Literal["alta", "media", "baja"] = "media"
    expected_evidence: str
    source_text: str | None = None
    reason: str | None = None


class Evidence(BaseModel):
    text: str
    source: str
    score: float = Field(ge=0, le=1)


class CompetencyEvaluation(BaseModel):
    competency: Competency
    evidence_level: Literal["no_evidenciado", "debil", "parcial", "claro", "fuerte"]
    evidence_score: float = Field(ge=0, le=4)
    weighted_score: float = Field(ge=0)
    evidences: list[Evidence]
    explanation: str


class CandidateEvaluation(BaseModel):
    candidate_id: str
    candidate_name: str
    total_score: float
    normalized_score: float = Field(ge=0, le=100)
    evaluations: list[CompetencyEvaluation]
    strengths: list[str]
    gaps: list[str]
    recommendation: Literal["recomendado", "considerable", "no_prioritario"]


class AnalysisRequest(BaseModel):
    announcement_id: str
    cv_ids: list[str]
    announcement_text_override: str | None = None
    terna_size: int = Field(default=3, ge=1, le=10)
    selected_model: str | None = None


class LLMStatus(BaseModel):
    provider: str
    use_llm: bool
    configured: bool
    model: str
    endpoint: str
    mode: str
    message: str


class AnalysisResponse(BaseModel):
    announcement_name: str
    competencies: list[Competency]
    candidates: list[CandidateEvaluation]
    ranking: list[CandidateEvaluation]
    recommended_terna: list[CandidateEvaluation]
    report: dict | None = None
    progress_log: list[str] = []
    agent_trace: dict[str, Any] | None = None
    observability: dict[str, Any] | None = None
    # Consumo acumulado de tokens y costo estimado del análisis.
    llm_usage: dict[str, Any] | None = None
