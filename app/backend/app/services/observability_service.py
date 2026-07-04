# observability_service.py

# Importaciones estándar utilizadas para construir la observabilidad:
# - json: guardar snapshots en formato JSON.
# - statistics: calcular promedios y desviación estándar.
# - time y datetime: medir duración y generar fechas legibles.
# - Path: manejar rutas de carpetas y archivos.
# - Any: permitir tipos flexibles en resultados y diccionarios.

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class ObservabilityService:
    """
    Servicio centralizado para construir métricas de observabilidad del agente.

    Este servicio no cambia la lógica principal del análisis, del RAG ni del LLM.
    Su responsabilidad es observar y resumir lo que ocurrió durante una ejecución.

    Permite registrar:
    - latencia total y latencia promedio;
    - uso de LLM, fallback local y errores;
    - calidad de evidencia documental;
    - comportamiento del ranking;
    - eventos de trazabilidad;
    - anomalías detectadas;
    - recomendaciones técnicas;
    - controles de uso responsable.
    """

    # Umbrales usados para detectar anomalías.
    # Se dejan como constantes para que sea fácil ajustarlos más adelante.
    FALLBACK_RATE_WARNING = 30.0
    WEAK_EVIDENCE_RATE_WARNING = 40.0
    TOP_CANDIDATE_MARGIN_WARNING = 5.0
    LATENCY_PER_EVALUATION_WARNING_SECONDS = 20.0

    def __init__(self, observability_dir: Path | None = None) -> None:
        """
        Inicializa el servicio.

        observability_dir:
            Carpeta donde se guardarán los archivos JSON de observabilidad.
            Si viene None, el servicio puede construir snapshots, pero no guardarlos.
        """

        self.observability_dir = observability_dir

        # Si existe una carpeta configurada, nos aseguramos de crearla.
        # parents=True permite crear carpetas intermedias.
        # exist_ok=True evita error si la carpeta ya existe.
        if self.observability_dir:
            self.observability_dir.mkdir(parents=True, exist_ok=True)

    def build_snapshot(
        self,
        *,
        result: Any,
        job_state: dict[str, Any] | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
    ) -> dict[str, Any]:
        """
        Construye un snapshot de observabilidad para una ejecución del agente.

        result:
            Resultado final del análisis. Puede ser un modelo Pydantic
            o un diccionario normal.

        job_state:
            Estado del job guardado por main.py. Desde aquí se obtienen:
            - job_id;
            - estado;
            - eventos;
            - contador de llamadas LLM exitosas;
            - contador de fallback;
            - contador de errores.

        started_at / finished_at:
            Tiempos en formato time.time(). Se usan para calcular latencia.
            Si no se entregan, se intenta usar el job_state o el momento actual.
        """

        result_dict = self._to_dict(result)
        job_state = job_state or {}

        now = time.time()

        # Se priorizan los tiempos recibidos por parámetro.
        # Si no existen, se usan los tiempos guardados en el job.
        started = started_at or float(job_state.get("created_at") or now)
        finished = finished_at or float(job_state.get("updated_at") or now)

        # Evitamos valores negativos en caso de algún desfase de tiempos.
        duration_seconds = max(0.0, round(finished - started, 3))

        ranking = result_dict.get("ranking") or []
        competencies = result_dict.get("competencies") or []
        candidates = result_dict.get("candidates") or []

        evaluation_count = self._count_evaluations(ranking)

        # Construimos bloques separados para mantener el código ordenado.
        llm_metrics = self._build_llm_metrics(job_state)
        quality_metrics = self._build_quality_metrics(ranking)
        ranking_metrics = self._build_ranking_metrics(ranking)

        snapshot = {
            "trace_id": job_state.get("job_id"),
            "status": job_state.get("status", "completed"),
            "created_at": self._format_timestamp(started),
            "finished_at": self._format_timestamp(finished),
            "duration_seconds": duration_seconds,
            "dataset": {
                "announcement_name": result_dict.get("announcement_name"),
                "candidate_count": len(candidates),
                "competency_count": len(competencies),
                "evaluation_count": evaluation_count,
            },
            "llm": llm_metrics,
            "performance": {
                "total_latency_seconds": duration_seconds,
                "average_latency_per_candidate_seconds": self._safe_divide(
                    duration_seconds,
                    max(len(candidates), 1),
                ),
                "average_latency_per_evaluation_seconds": self._safe_divide(
                    duration_seconds,
                    max(evaluation_count, 1),
                ),
            },
            "ranking": ranking_metrics,
            "quality": quality_metrics,
            "responsible_ai": self._build_responsible_ai_summary(),
            "events": self._normalize_events(job_state.get("events") or []),
        }

        # Las anomalías y recomendaciones se calculan al final porque dependen
        # de los bloques anteriores: llm, performance, quality y ranking.
        snapshot["anomalies"] = self._detect_anomalies(snapshot)
        snapshot["recommendations"] = self._build_recommendations(snapshot)

        return snapshot

    def save_snapshot(self, snapshot: dict[str, Any]) -> dict[str, str] | None:
        """
        Guarda el snapshot de observabilidad en formato JSON.

        Retorna un diccionario con información del archivo generado.
        Si no hay carpeta de observabilidad configurada, retorna None.
        """

        if not self.observability_dir:
            return None

        trace_id = str(snapshot.get("trace_id") or "directo")
        safe_trace_id = trace_id[:8] if trace_id else "directo"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"observability_{timestamp}_{safe_trace_id}.json"
        path = self.observability_dir / filename

        # ensure_ascii=False mantiene tildes y caracteres en español.
        # Para ver bien el archivo en PowerShell, usar Get-Content -Encoding utf8.
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "filename": filename,
            "path": str(path),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _build_llm_metrics(self, job_state: dict[str, Any]) -> dict[str, Any]:
        """
        Construye métricas de uso del modelo LLM.

        Estas métricas permiten observar:
        - cuántas llamadas fueron exitosas;
        - cuántas veces se usó fallback local;
        - cuántos errores ocurrieron;
        - tasas porcentuales asociadas.
        """

        llm_success = int(job_state.get("llm_success") or 0)
        llm_fallback = int(job_state.get("llm_fallback") or 0)
        llm_errors = int(job_state.get("llm_errors") or 0)

        total = llm_success + llm_fallback + llm_errors

        return {
            "success_count": llm_success,
            "fallback_count": llm_fallback,
            "error_count": llm_errors,
            "total_events": total,
            "success_rate": self._safe_rate(llm_success, total),
            "fallback_rate": self._safe_rate(llm_fallback, total),
            "error_rate": self._safe_rate(llm_errors, total),
        }

    def _build_quality_metrics(self, ranking: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Construye métricas aproximadas de calidad del output.

        En este proyecto, la calidad se observa a partir del puntaje de evidencia
        de cada competencia evaluada.

        Escala esperada:
        0 = no evidenciado
        1 = débil
        2 = parcial
        3 = claro
        4 = fuerte
        """

        evidence_scores: list[float] = []

        for candidate in ranking:
            for evaluation in candidate.get("evaluations") or []:
                try:
                    evidence_scores.append(float(evaluation.get("evidence_score", 0)))
                except (TypeError, ValueError):
                    # Si algún dato viene mal formado, no rompemos el snapshot.
                    # Lo registramos como 0 para mantener continuidad.
                    evidence_scores.append(0.0)

        weak_count = sum(1 for score in evidence_scores if score <= 1)
        strong_count = sum(1 for score in evidence_scores if score >= 3)
        total = len(evidence_scores)

        return {
            "average_evidence_score": round(statistics.mean(evidence_scores), 2)
            if evidence_scores
            else 0.0,
            "weak_evidence_count": weak_count,
            "strong_evidence_count": strong_count,
            "weak_evidence_rate": self._safe_rate(weak_count, total),
            "strong_evidence_rate": self._safe_rate(strong_count, total),
        }

    def _build_ranking_metrics(self, ranking: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Construye métricas del ranking.

        Estas métricas ayudan a revisar si el ranking está muy concentrado,
        si hay diferencias claras entre candidatos o si se requiere una revisión
        humana más detallada.
        """

        scores: list[float] = []

        for candidate in ranking:
            try:
                scores.append(float(candidate.get("normalized_score", 0)))
            except (TypeError, ValueError):
                scores.append(0.0)

        if not scores:
            return {
                "average_score": 0.0,
                "max_score": 0.0,
                "min_score": 0.0,
                "score_std_dev": 0.0,
                "top_candidate_margin": 0.0,
            }

        # Se asume que ranking ya viene ordenado de mayor a menor puntaje.
        # Si solo hay un candidato, el margen no aplica y queda en 0.
        top_margin = 0.0
        if len(scores) >= 2:
            top_margin = round(scores[0] - scores[1], 2)

        return {
            "average_score": round(statistics.mean(scores), 2),
            "max_score": round(max(scores), 2),
            "min_score": round(min(scores), 2),
            "score_std_dev": round(statistics.pstdev(scores), 2),
            "top_candidate_margin": top_margin,
        }

    def _build_responsible_ai_summary(self) -> dict[str, Any]:
        """
        Resume controles de seguridad, privacidad y uso responsable.

        Este bloque ayuda a evidenciar que el agente se usa como apoyo
        a la preselección documental y no como mecanismo automático de contratación.
        """

        return {
            "decision_scope": "apoyo_documental",
            "human_decision_required": True,
            "sensitive_attributes_ignored": [
                "edad",
                "género",
                "nacionalidad",
                "estado civil",
                "fotografía",
                "dirección exacta",
                "datos familiares",
                "religión",
                "situación médica",
                "opiniones políticas",
            ],
            "allowed_evidence_basis": [
                "formación académica",
                "experiencia laboral",
                "conocimientos técnicos",
                "certificaciones",
                "funciones realizadas",
                "competencias relacionadas con el cargo",
            ],
            "privacy_controls": [
                "los archivos se procesan localmente dentro del prototipo",
                "la evaluación se basa en fragmentos recuperados desde el CV",
                "el reporte técnico mantiene trazabilidad del análisis realizado",
            ],
            "responsible_use_note": (
                "El ranking y la terna recomendada no constituyen una decisión automática "
                "de contratación. Deben ser revisados por una persona responsable del proceso."
            ),
        }

    def _detect_anomalies(self, snapshot: dict[str, Any]) -> list[dict[str, str]]:
        """
        Detecta anomalías simples a partir de las métricas observadas.

        No es un sistema estadístico complejo. Es una capa práctica de reglas
        para identificar situaciones relevantes en una demo académica:
        - alto uso de fallback;
        - errores del LLM;
        - baja evidencia documental;
        - ranking muy estrecho;
        - latencia alta.
        """

        anomalies: list[dict[str, str]] = []

        llm = snapshot.get("llm") or {}
        quality = snapshot.get("quality") or {}
        ranking = snapshot.get("ranking") or {}
        performance = snapshot.get("performance") or {}
        dataset = snapshot.get("dataset") or {}

        if llm.get("fallback_rate", 0) >= self.FALLBACK_RATE_WARNING:
            anomalies.append(
                {
                    "type": "alto_uso_fallback",
                    "severity": "media",
                    "message": (
                        "Se detectó un uso elevado de fallback local. "
                        "Puede indicar límites del LLM, errores temporales o problemas de configuración."
                    ),
                }
            )

        if llm.get("error_rate", 0) > 0:
            anomalies.append(
                {
                    "type": "errores_llm",
                    "severity": "alta",
                    "message": "Se registraron errores asociados al uso del modelo LLM.",
                }
            )

        if quality.get("weak_evidence_rate", 0) >= self.WEAK_EVIDENCE_RATE_WARNING:
            anomalies.append(
                {
                    "type": "baja_evidencia_documental",
                    "severity": "media",
                    "message": (
                        "Una proporción relevante de evaluaciones tiene evidencia débil "
                        "o no evidenciada."
                    ),
                }
            )

        # El ranking estrecho solo tiene sentido si hay más de un candidato.
        if (
            dataset.get("candidate_count", 0) > 1
            and ranking.get("top_candidate_margin", 0) < self.TOP_CANDIDATE_MARGIN_WARNING
        ):
            anomalies.append(
                {
                    "type": "ranking_estrecho",
                    "severity": "baja",
                    "message": (
                        "La diferencia entre los primeros candidatos es baja. "
                        "Conviene revisión humana detallada antes de decidir."
                    ),
                }
            )

        if (
            performance.get("average_latency_per_evaluation_seconds", 0)
            > self.LATENCY_PER_EVALUATION_WARNING_SECONDS
        ):
            anomalies.append(
                {
                    "type": "latencia_alta",
                    "severity": "media",
                    "message": (
                        "La latencia promedio por evaluación es alta. "
                        "Puede afectar la escalabilidad del sistema."
                    ),
                }
            )

        return anomalies

    def _build_recommendations(self, snapshot: dict[str, Any]) -> list[str]:
        """
        Genera recomendaciones técnicas basadas en las anomalías observadas.

        Estas recomendaciones sirven como evidencia para la pauta EP3, porque
        conectan métricas reales con mejoras concretas del agente.
        """

        recommendations: list[str] = []

        llm = snapshot.get("llm") or {}
        quality = snapshot.get("quality") or {}
        performance = snapshot.get("performance") or {}
        ranking = snapshot.get("ranking") or {}
        dataset = snapshot.get("dataset") or {}

        if llm.get("fallback_rate", 0) >= self.FALLBACK_RATE_WARNING:
            recommendations.append(
                "Revisar la configuración del modelo LLM, límites de solicitudes y tiempos de espera "
                "para reducir el uso de fallback local."
            )

        if quality.get("weak_evidence_rate", 0) >= self.WEAK_EVIDENCE_RATE_WARNING:
            recommendations.append(
                "Revisar la calidad de los CV o ajustar las consultas RAG, porque varias competencias "
                "tienen evidencia documental débil."
            )

        if (
            performance.get("average_latency_per_evaluation_seconds", 0)
            > self.LATENCY_PER_EVALUATION_WARNING_SECONDS
        ):
            recommendations.append(
                "Optimizar el número de llamadas al LLM o reducir el tamaño de evidencia enviada "
                "por competencia para mejorar latencia."
            )

        if (
            dataset.get("candidate_count", 0) > 1
            and ranking.get("top_candidate_margin", 0) < self.TOP_CANDIDATE_MARGIN_WARNING
        ):
            recommendations.append(
                "Revisar manualmente a los candidatos mejor posicionados, porque el ranking muestra "
                "diferencias estrechas."
            )

        if not recommendations:
            recommendations.append(
                "La ejecución no muestra anomalías críticas. Mantener revisión humana final y monitorear "
                "nuevas ejecuciones para comparar estabilidad."
            )

        # Esta recomendación se agrega siempre porque forma parte del uso responsable.
        recommendations.append(
            "Mantener el sistema como apoyo a la preselección documental; la decisión final debe permanecer "
            "en una persona responsable del proceso."
        )

        return recommendations

    def _count_evaluations(self, ranking: list[dict[str, Any]]) -> int:
        """
        Cuenta cuántas evaluaciones competencia-candidato existen en el ranking.
        """

        return sum(len(candidate.get("evaluations") or []) for candidate in ranking)

    def _normalize_events(self, events: list[dict[str, Any]]) -> list[dict[str, str]]:
        """
        Normaliza los eventos para que el JSON de observabilidad sea consistente.

        Esto evita errores si algún evento viene incompleto o con tipos inesperados.
        """

        normalized: list[dict[str, str]] = []

        for event in events:
            normalized.append(
                {
                    "time": str(event.get("time", "")),
                    "kind": str(event.get("kind", "info")),
                    "message": str(event.get("message", "")),
                }
            )

        return normalized

    def _to_dict(self, value: Any) -> dict[str, Any]:
        """
        Convierte distintos tipos de resultado a diccionario.

        Soporta:
        - modelos Pydantic v2 mediante model_dump;
        - diccionarios normales;
        - otros objetos serializables a JSON.
        """

        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")

        if isinstance(value, dict):
            return value

        return json.loads(json.dumps(value, default=str))

    def _safe_rate(self, numerator: int | float, denominator: int | float) -> float:
        """
        Calcula un porcentaje evitando división por cero.
        """

        if not denominator:
            return 0.0

        return round((float(numerator) / float(denominator)) * 100, 2)

    def _safe_divide(self, numerator: int | float, denominator: int | float) -> float:
        """
        Divide dos números evitando división por cero.
        """

        if not denominator:
            return 0.0

        return round(float(numerator) / float(denominator), 3)

    def _format_timestamp(self, timestamp_value: float) -> str:
        """
        Convierte un timestamp numérico a formato ISO legible.
        """

        return datetime.fromtimestamp(timestamp_value).isoformat(timespec="seconds")