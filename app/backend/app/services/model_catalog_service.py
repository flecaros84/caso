from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings


class ModelCatalogService:
    """Reads the local catalog of GitHub Models available for the frontend selector."""

    def __init__(self, catalog_path: Path | None = None) -> None:
        self.catalog_path = catalog_path or Path(__file__).resolve().parents[1] / "data" / "github_models.json"

    def list_models(self) -> list[dict[str, Any]]:
        if not self.catalog_path.exists():
            return [self._default_model()]

        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except Exception:
            return [self._default_model()]

        if not isinstance(data, list):
            return [self._default_model()]

        models: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id", "")).strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            models.append({
                "id": model_id,
                "name": str(item.get("name") or model_id).strip(),
                "provider": str(item.get("provider") or "GitHub Models").strip(),
                "description": str(item.get("description") or "").strip(),
                "default": bool(item.get("default", False)) or model_id == settings.github_model,
            })

        if not any(model["id"] == settings.github_model for model in models):
            default = self._default_model()
            default["default"] = True
            models.insert(0, default)

        return models or [self._default_model()]

    def is_allowed_model(self, model_id: str | None) -> bool:
        if not model_id:
            return True
        return any(model["id"] == model_id for model in self.list_models())

    def get_default_model(self) -> str:
        models = self.list_models()
        for model in models:
            if model.get("default"):
                return str(model["id"])
        return settings.github_model

    def _default_model(self) -> dict[str, Any]:
        return {
            "id": settings.github_model,
            "name": settings.github_model,
            "provider": "GitHub Models",
            "description": "Modelo configurado por defecto en GITHUB_MODEL.",
            "default": True,
        }
