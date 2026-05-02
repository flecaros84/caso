from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.models.schemas import AnalysisRequest, AnalysisResponse
from app.services.file_service import FileService
from app.services.text_extractor import TextExtractor
from app.services.competency_service import CompetencyService
from app.services.rag_service import SimpleRAGIndex
from app.services.evaluator_service import EvaluatorService
from app.services.ranking_service import RankingService
from app.services.llm_client import GitHubModelsClient
from app.services.report_service import ReportService
from app.services.model_catalog_service import ModelCatalogService

# main.py lives in: caso/app/backend/app/main.py
APP_DIR = Path(__file__).resolve().parents[2]      # caso/app
PROJECT_DIR = APP_DIR.parent                       # caso

# Accept both layouts:
# 1) caso/app/frontend
# 2) caso/frontend
FRONTEND_CANDIDATES = [
    APP_DIR / "frontend",
    PROJECT_DIR / "frontend",
]
FRONTEND_DIR = next((p for p in FRONTEND_CANDIDATES if p.exists()), FRONTEND_CANDIDATES[0])

app = FastAPI(
    title="Talent RAG - Evaluador por Competencias",
    version="1.1.0",
    description="Sistema RAG para extraer competencias desde anuncios laborales y generar ranking de candidatos por terna.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

file_service = FileService()
text_extractor = TextExtractor()
competency_service = CompetencyService()
evaluator_service = EvaluatorService()
ranking_service = RankingService()
report_service = ReportService(APP_DIR / "backend" / "outputs" / "reports")
model_catalog_service = ModelCatalogService()

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _new_job() -> str:
    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "completed_steps": 0,
            "total_steps": 5,
            "progress": 0,
            "current_step": "En cola...",
            "llm_success": 0,
            "llm_fallback": 0,
            "llm_errors": 0,
            "events": [],
            "event_count": 0,
            "result": None,
            "error": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    return job_id


def _update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(changes)
        total = max(int(job.get("total_steps") or 1), 1)
        completed = max(int(job.get("completed_steps") or 0), 0)
        job["progress"] = min(100, round((completed / total) * 100))
        job["updated_at"] = time.time()


def _add_event(job_id: str, message: str, kind: str = "info") -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        events = job.setdefault("events", [])
        events.append({"time": time.strftime("%H:%M:%S"), "kind": kind, "message": message})
        job["events"] = events
        job["event_count"] = len(events)
        job["updated_at"] = time.time()


def _advance_job(job_id: str | None, message: str, kind: str = "info", steps: int = 1) -> None:
    if not job_id:
        return
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["completed_steps"] = int(job.get("completed_steps") or 0) + steps
        job["current_step"] = message
        total = max(int(job.get("total_steps") or 1), 1)
        job["progress"] = min(100, round((job["completed_steps"] / total) * 100))
        events = job.setdefault("events", [])
        events.append({"time": time.strftime("%H:%M:%S"), "kind": kind, "message": message})
        job["events"] = events
        job["event_count"] = len(events)
        job["updated_at"] = time.time()


def get_llm_status(model_override: str | None = None) -> dict:
    """Returns safe LLM configuration status for the frontend.

    Important: never expose the GitHub token value.
    """
    client = GitHubModelsClient(model=model_override)
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


def run_analysis(request: AnalysisRequest, job_id: str | None = None) -> AnalysisResponse:
    announcement_path = file_service.resolve_announcement(request.announcement_id)

    selected_model = (request.selected_model or model_catalog_service.get_default_model()).strip()
    if not model_catalog_service.is_allowed_model(selected_model):
        raise ValueError(f"Modelo no permitido o no registrado en github_models.json: {selected_model}")

    llm_client = GitHubModelsClient(model=selected_model)
    local_competency_service = CompetencyService(llm=llm_client)
    local_evaluator_service = EvaluatorService(llm=llm_client)

    if job_id:
        _add_event(job_id, f"Modelo seleccionado: {selected_model}", "info")

    announcement_text = (request.announcement_text_override or "").strip()

    _advance_job(job_id, "Preparando anuncio laboral...")

    # Only use OCR/file extraction when the user did not paste manual text.
    if not announcement_text:
        announcement_text = text_extractor.extract(announcement_path)

    if not announcement_text.strip():
        raise ValueError(
            "No se pudo extraer texto del anuncio. Instala Tesseract OCR o pega el texto del anuncio manualmente en el frontend."
        )

    _advance_job(job_id, "Texto del anuncio disponible.")
    _add_event(job_id, "Deduciendo competencias desde el anuncio con IA/RAG...", "info") if job_id else None

    competencies = local_competency_service.extract_competencies(announcement_text)

    extraction_mode = getattr(local_competency_service, "last_extraction_mode", "fallback")
    if job_id:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                if extraction_mode == "llm":
                    job["llm_success"] = int(job.get("llm_success") or 0) + 1
                elif extraction_mode == "fallback":
                    job["llm_fallback"] = int(job.get("llm_fallback") or 0) + 1
                elif extraction_mode == "empty":
                    job["llm_errors"] = int(job.get("llm_errors") or 0) + 1

    if extraction_mode == "llm":
        competency_mode_label = "LLM OK"
        competency_event_kind = "success"
    elif extraction_mode == "fallback":
        competency_mode_label = "fallback local"
        competency_event_kind = "warning"
    else:
        competency_mode_label = "sin competencias"
        competency_event_kind = "error"

    _advance_job(
        job_id,
        f"Competencias deducidas: {len(competencies)} ({competency_mode_label}).",
        kind=competency_event_kind,
    )

    total_steps = 3 + len(request.cv_ids) * (2 + max(len(competencies), 1)) + 1
    if job_id:
        _update_job(job_id, total_steps=total_steps, status="running")

    candidate_results = []

    for cv_position, cv_id in enumerate(request.cv_ids, start=1):
        cv_path = file_service.resolve_cv(cv_id)
        _advance_job(job_id, f"Leyendo CV {cv_position}/{len(request.cv_ids)}: {cv_path.name}")
        cv_text = text_extractor.extract(cv_path)

        _advance_job(job_id, f"Construyendo índice RAG para {cv_path.name}")
        index = SimpleRAGIndex(candidate_id=cv_id, candidate_name=cv_path.name, text=cv_text)

        def evaluation_progress(payload: dict[str, Any]) -> None:
            llm_success = bool(payload.get("llm_success"))
            used_fallback = bool(payload.get("used_fallback"))
            comp_name = str(payload.get("competency_name", "competencia"))
            level = str(payload.get("evidence_level", ""))
            candidate_name = str(payload.get("candidate_name", cv_path.name))

            if job_id:
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    if job:
                        if llm_success:
                            job["llm_success"] = int(job.get("llm_success") or 0) + 1
                        elif used_fallback:
                            job["llm_fallback"] = int(job.get("llm_fallback") or 0) + 1

            kind = "success" if llm_success else "warning"
            mode = "LLM OK" if llm_success else "fallback local"
            _advance_job(
                job_id,
                f"{candidate_name}: {comp_name} evaluada ({mode}, nivel: {level}).",
                kind=kind,
            )

        evaluations = local_evaluator_service.evaluate_candidate(index, competencies, progress_callback=evaluation_progress)
        candidate_results.append(
            ranking_service.build_candidate_result(
                candidate_id=cv_id,
                candidate_filename=cv_path.name,
                evaluations=evaluations,
            )
        )

    ranking = ranking_service.rank(candidate_results)
    terna = ranking[: request.terna_size]
    _advance_job(job_id, "Ranking y terna generados.", kind="success")

    response = AnalysisResponse(
        announcement_id=request.announcement_id,
        competencies=competencies,
        ranking=ranking,
        terna=terna,
        ethical_notes=[
            "El ranking es una preselección documental y no reemplaza la decisión humana.",
            "El sistema debe ignorar edad, género, fotografía, nacionalidad, estado civil y datos familiares.",
            "Las recomendaciones se basan en evidencia textual presente en los CV y en el anuncio laboral.",
        ],
        llm_status=get_llm_status(selected_model),
    )

    report_info = report_service.save_analysis_report(
        result=response,
        job_id=job_id,
        announcement_id=request.announcement_id,
    )
    response.report = report_info

    if job_id:
        _add_event(
            job_id,
            f"Reporte local generado: {report_info['markdown_filename']}",
            kind="success",
        )

    return response


def _background_analysis(job_id: str, request: AnalysisRequest) -> None:
    try:
        _update_job(job_id, status="running", current_step="Iniciando análisis...")
        result = run_analysis(request, job_id=job_id)
        _update_job(
            job_id,
            status="completed",
            completed_steps=JOBS[job_id]["total_steps"],
            current_step="Análisis completado.",
            result=result.model_dump(mode="json"),
        )
        _add_event(job_id, "Análisis completado correctamente.", "success")
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc), current_step="El análisis falló.")
        _add_event(job_id, f"Error: {exc}", "error")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/api/files")
def list_files() -> dict:
    return {
        "announcements": file_service.list_announcements(),
        "cvs": file_service.list_cvs(),
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "frontend_dir": str(FRONTEND_DIR),
        "announcements_dir": str(file_service.announcements_dir),
        "cv_dir": str(file_service.cv_dir),
        "llm": get_llm_status(),
        "reports_dir": str(report_service.reports_dir),
    }


@app.get("/api/llm/status")
def llm_status() -> dict:
    return get_llm_status()


@app.get("/api/llm/models")
def llm_models() -> dict:
    return {
        "default_model": model_catalog_service.get_default_model(),
        "models": model_catalog_service.list_models(),
    }


@app.get("/api/reports")
def list_reports() -> dict:
    return {"reports": report_service.list_reports()}


@app.get("/api/reports/{filename}")
def download_report(filename: str) -> FileResponse:
    try:
        path = report_service.resolve_report(filename)
        media_type = "application/json" if path.suffix.lower() == ".json" else "text/markdown"
        return FileResponse(path, media_type=media_type, filename=path.name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/extract/announcement/{announcement_id}")
def extract_announcement(announcement_id: str) -> dict:
    try:
        path = file_service.resolve_announcement(announcement_id)
        text = text_extractor.extract(path)
        return {"id": announcement_id, "filename": path.name, "text": text}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyze", response_model=AnalysisResponse)
def analyze(request: AnalysisRequest) -> AnalysisResponse:
    try:
        return run_analysis(request)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyze/start")
def analyze_start(request: AnalysisRequest) -> dict:
    job_id = _new_job()
    thread = threading.Thread(target=_background_analysis, args=(job_id, request), daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.get("/api/analyze/status/{job_id}")
def analyze_status(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Trabajo de análisis no encontrado.")
        safe_job = {k: v for k, v in job.items() if k != "result"}
    return safe_job


@app.get("/api/analyze/result/{job_id}")
def analyze_result(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Trabajo de análisis no encontrado.")
        if job.get("status") != "completed":
            raise HTTPException(status_code=202, detail="El análisis todavía no ha terminado.")
        return job["result"]
