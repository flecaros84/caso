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
        self.session_id = str(uuid.uuid4())
        self.created_at = datetime.now().isoformat(timespec="seconds")

        self.steps: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.context_items: list[dict[str, Any]] = []
        self.final_summary: dict[str, Any] = {}

        if memory_path is None:
            backend_root = Path(__file__).resolve().parents[2]
            memory_path = backend_root / "outputs" / "memory" / "agent_memory.json"

        self.memory_path = Path(memory_path)
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)

    def add_step(self, name: str, status: str = "completed", detail: str = "") -> None:
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
        self.context_items.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source": source,
                "content_summary": content_summary,
                "metadata": metadata or {},
            }
        )

    def set_final_summary(self, summary: dict[str, Any]) -> None:
        self.final_summary = summary

    def to_trace(self) -> dict[str, Any]:
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
        if not self.memory_path.exists():
            return []

        try:
            with self.memory_path.open("r", encoding="utf-8") as file:
                data = json.load(file)

            if isinstance(data, list):
                return data

            return []
        except (json.JSONDecodeError, OSError):
            return []

    def save_long_term_memory(self) -> None:
        previous_sessions = self.load_long_term_memory()

        session_record = {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "steps_count": len(self.steps),
            "decisions_count": len(self.decisions),
            "tool_calls_count": len(self.tool_calls),
            "context_items_count": len(self.context_items),
            "final_summary": self.final_summary,
        }

        previous_sessions.append(session_record)

        with self.memory_path.open("w", encoding="utf-8") as file:
            json.dump(previous_sessions, file, ensure_ascii=False, indent=2)