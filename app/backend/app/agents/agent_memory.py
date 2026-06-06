# app/backend/app/agents/agent_memory.py

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentMemory:
    """
    Stores short-term and long-term memory for the recruitment agent.

    Short-term memory lives during the current execution.
    Long-term memory is persisted in a local JSON file.
    """

    def __init__(self, memory_path: str | Path | None = None) -> None:
        # Genera un identificador único para esta ejecución del agente.
        self.session_id = str(uuid.uuid4())

        # Guarda la fecha y hora de creación de la sesión.
        self.created_at = datetime.now().isoformat(timespec="seconds")

        # Memoria de corto plazo de la ejecución actual.
        # Cada lista registra un tipo distinto de información trazable.
        self.steps: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.context_items: list[dict[str, Any]] = []
        self.final_summary: dict[str, Any] = {}

        # Si no se entrega una ruta personalizada, se usa la ruta local por defecto.
        if memory_path is None:
            backend_root = Path(__file__).resolve().parents[2]
            memory_path = backend_root / "outputs" / "memory" / "agent_memory.json"

        # Guarda la ruta donde se persistirá la memoria de largo plazo.
        self.memory_path = Path(memory_path)

        # Crea la carpeta de memoria si todavía no existe.
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

    def add_step(self, name: str, status: str = "completed", detail: str = "") -> None:
        # Registra un paso del flujo ejecutado por el agente.
        # Sirve para reconstruir el avance general del análisis.
        self.steps.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "name": name,
                "status": status,
                "detail": detail,
            }
        )

    def add_decision(
        self,
        decision: str,
        reason: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Registra una decisión tomada por el agente.
        # Incluye la razón, el resultado y metadatos opcionales.
        self.decisions.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "decision": decision,
                "reason": reason,
                "outcome": outcome,
                "metadata": metadata or {},
            }
        )

    def add_tool_call(
        self,
        tool_name: str,
        input_summary: str = "",
        output_summary: str = "",
        success: bool = True,
    ) -> None:
        # Registra el uso de una herramienta del agente.
        # Esto ayuda a auditar qué herramienta se llamó, con qué entrada y con qué resultado.
        self.tool_calls.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "tool_name": tool_name,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "success": success,
            }
        )

    def add_context_item(
        self,
        source: str,
        content_summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Registra información contextual relevante para la sesión.
        # Por ejemplo, un resumen de un candidato evaluado o de un documento procesado.
        self.context_items.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source": source,
                "content_summary": content_summary,
                "metadata": metadata or {},
            }
        )

    def set_final_summary(self, summary: dict[str, Any]) -> None:
        # Guarda el resumen final de la ejecución actual del agente.
        self.final_summary = summary

    def to_trace(self) -> dict[str, Any]:
        # Construye una traza completa de la memoria de corto plazo.
        # Esta estructura puede adjuntarse a la respuesta final del análisis.
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "short_term_memory": {
                "steps": self.steps,
                "decisions": self.decisions,
                "tool_calls": self.tool_calls,
                "context_items": self.context_items,
                "final_summary": self.final_summary,
            },
            "long_term_memory_path": str(self.memory_path),
        }

    def load_long_term_memory(self) -> list[dict[str, Any]]:
        # Si el archivo de memoria aún no existe, no hay sesiones anteriores que cargar.
        if not self.memory_path.exists():
            return []

        try:
            # Lee el archivo JSON donde se guardan las sesiones anteriores.
            with self.memory_path.open("r", encoding="utf-8") as file:
                data = json.load(file)

            # La memoria persistente debe ser una lista de sesiones.
            if isinstance(data, list):
                return data

            # Si el contenido no tiene el formato esperado, se ignora para evitar errores.
            return []
        except (json.JSONDecodeError, OSError):
            # Si el archivo está corrupto o no se puede leer, se devuelve una lista vacía.
            return []

    def save_long_term_memory(self) -> None:
        # Carga las sesiones anteriores para no sobrescribir el historial completo.
        previous_sessions = self.load_long_term_memory()

        # Construye un registro resumido de la sesión actual.
        # No guarda todo el detalle, sino conteos y el resumen final.
        session_record = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "steps_count": len(self.steps),
            "decisions_count": len(self.decisions),
            "tool_calls_count": len(self.tool_calls),
            "context_items_count": len(self.context_items),
            "final_summary": self.final_summary,
        }

        # Agrega la sesión actual al historial de memoria persistente.
        previous_sessions.append(session_record)

        # Guarda nuevamente el historial completo en formato JSON legible.
        with self.memory_path.open("w", encoding="utf-8") as file:
            json.dump(previous_sessions, file, ensure_ascii=False, indent=2)