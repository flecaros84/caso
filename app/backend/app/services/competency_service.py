from __future__ import annotations

import re
from typing import Any

from app.models.schemas import Competency
from app.services.llm_client import GitHubModelsClient


class CompetencyService:
    """
    Extracts required competencies from any job announcement.

    It is intentionally not tied to a specific role. The expected flow is:
    announcement text -> dynamic competencies -> CV evaluation.
    """

    VALID_CATEGORIES = {"tecnica", "experiencia", "formacion", "transversal", "contextual", "requisito_formal"}
    VALID_IMPORTANCE = {"alta", "media", "baja"}

    def __init__(self, llm: GitHubModelsClient | None = None) -> None:
        self.llm = llm or GitHubModelsClient()
        # Metadata used by the progress panel.
        # Values: "not_started", "llm", "fallback", "empty".
        self.last_extraction_mode = "not_started"

    def extract_competencies(self, announcement_text: str) -> list[Competency]:
        text = self._clean_text(announcement_text)
        if not text:
            self.last_extraction_mode = "empty"
            return []

        llm_result = self._extract_with_llm(text)
        if llm_result:
            self.last_extraction_mode = "llm"
            return self._normalize_weights(llm_result)

        self.last_extraction_mode = "fallback"
        return self._extract_with_rules(text)

    def _extract_with_llm(self, text: str) -> list[Competency] | None:
        system = """
Eres un especialista en selección por competencias.
Debes deducir competencias requeridas desde el anuncio laboral entregado.

Reglas:
- Extrae competencias solamente desde el texto del anuncio.
- No uses una plantilla fija ni competencias predeterminadas.
- No inventes requisitos no presentes en el anuncio.
- Ignora edad, género, nacionalidad, fotografía, estado civil, domicilio, familia u otras variables sensibles.
- Responde solamente JSON válido.
""".strip()

        user = f"""
Analiza el siguiente anuncio laboral y deduce entre 4 y 8 competencias requeridas.

Cada competencia debe incluir:
- name: nombre breve de la competencia.
- category: una de ["tecnica", "experiencia", "formacion", "transversal", "contextual", "requisito_formal"].
- weight: número decimal entre 0.05 y 0.35. La suma debe aproximarse a 1.0.
- importance: una de ["alta", "media", "baja"].
- expected_evidence: evidencia que debería encontrarse en un CV para respaldar esa competencia.
- source_text: frase breve del anuncio que respalda la competencia.
- reason: explicación breve de por qué importa para el cargo.

Anuncio laboral:
--- INICIO ANUNCIO ---
{text[:9000]}
--- FIN ANUNCIO ---

Formato exacto:
{{
  "competencies": [
    {{
      "name": "Nombre de la competencia",
      "category": "tecnica",
      "weight": 0.20,
      "importance": "alta",
      "expected_evidence": "Evidencia esperada en el CV.",
      "source_text": "Fragmento del anuncio.",
      "reason": "Motivo breve."
    }}
  ]
}}
""".strip()

        data = self.llm.complete_json(system, user, max_tokens=1800)
        if not isinstance(data, dict):
            return None

        items = data.get("competencies", [])
        if not isinstance(items, list):
            return None

        competencies: list[Competency] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            comp = self._dict_to_competency(item)
            if comp:
                competencies.append(comp)

        return competencies[:8] or None

    def _extract_with_rules(self, text: str) -> list[Competency]:
        """
        Generic fallback. It extracts competency candidates from sections, bullets and requirement-like sentences.
        It does not contain job-specific fixed competencies.
        """
        sections = self._split_into_sections(text)
        candidates: list[Competency] = []

        for section_name, section_text in sections.items():
            for item in self._extract_items(section_text):
                comp = self._item_to_competency(item, section_name)
                if comp:
                    candidates.append(comp)

        if not candidates:
            for item in self._extract_items(text):
                comp = self._item_to_competency(item, "general")
                if comp:
                    candidates.append(comp)

        candidates = self._deduplicate(candidates)
        candidates = candidates[:8]

        return self._normalize_weights(candidates)

    def _split_into_sections(self, text: str) -> dict[str, str]:
        heading_patterns = {
            "mission": r"^(mis[ií]on del cargo|objetivo del cargo|prop[oó]sito del cargo)\s*:?.*$",
            "requirements": r"^(requisitos|requerimientos|perfil requerido|perfil del cargo)\s*:?.*$",
            "functions": r"^(funciones|responsabilidades|principales funciones|tareas)\s*:?.*$",
            "knowledge": r"^(conocimientos|herramientas|tecnolog[ií]as|software)\s*:?.*$",
            "experience": r"^(experiencia|trayectoria|experiencia requerida)\s*:?.*$",
            "education": r"^(formaci[oó]n|estudios|educaci[oó]n)\s*:?.*$",
        }

        lines = text.split("\n")
        sections: dict[str, list[str]] = {}
        current = "general"

        for line in lines:
            stripped = line.strip()
            matched_heading = None

            # Detect headings only when they are standalone-ish lines, not when the word
            # appears inside a requirement bullet such as "3 años de experiencia".
            for name, pattern in heading_patterns.items():
                if re.match(pattern, stripped, flags=re.IGNORECASE) and len(stripped) <= 80:
                    matched_heading = name
                    break

            if matched_heading:
                current = matched_heading
                sections.setdefault(current, []).append(stripped)
            else:
                sections.setdefault(current, []).append(line)

        result = {name: "\n".join(parts).strip() for name, parts in sections.items() if "\n".join(parts).strip()}
        return result or {"general": text}

    def _extract_items(self, text: str) -> list[str]:
        items: list[str] = []
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        for line in lines:
            cleaned = re.sub(r"^[\-\•\*\●\▪\–]\s*", "", line).strip()
            cleaned = re.sub(r"^\d+[\.|\)]\s*", "", cleaned).strip()
            if self._looks_relevant(cleaned):
                items.append(cleaned)

        # If there are few bullets, split long paragraphs into requirement-like clauses.
        if len(items) < 3:
            clauses = re.split(r"(?<=[\.\;])\s+|\n+", text)
            for clause in clauses:
                clause = clause.strip(" .;:-")
                if self._looks_relevant(clause):
                    items.append(clause)

        return self._deduplicate_text(items)

    def _looks_relevant(self, text: str) -> bool:
        if len(text) < 12:
            return False

        lowered = text.lower()
        generic_indicators = [
            "experiencia", "años", "año", "conocimiento", "conocimientos", "manejo", "dominio",
            "formación", "formacion", "título", "titulo", "profesional", "técnico", "tecnico",
            "licencia", "certificación", "certificacion", "curso", "disponibilidad", "turno", "turnos",
            "terreno", "faena", "funciones", "responsabilidades", "gestionar", "coordinar", "elaborar",
            "analizar", "supervisar", "ejecutar", "implementar", "controlar", "reportar",
            "normativa", "procedimientos", "herramientas", "software", "sistema", "sistemas", "excel",
            "comunicación", "comunicacion", "liderazgo", "equipo", "cliente", "proveedor",
        ]
        return any(token in lowered for token in generic_indicators)

    def _item_to_competency(self, item: str, section_name: str) -> Competency | None:
        name = self._generate_name(item)
        if not name:
            return None

        category = self._infer_category(item, section_name)
        importance = self._infer_importance(item, section_name)

        return Competency(
            name=name,
            category=category,
            weight=self._initial_weight(category, importance),
            importance=importance,
            expected_evidence=self._expected_evidence(item, category),
            source_text=item[:280],
            reason="Competencia deducida desde el texto del anuncio laboral.",
        )

    def _generate_name(self, item: str) -> str:
        text = re.sub(r"\s+", " ", item).strip(" .;:-")
        text = re.sub(r"^(requisitos?|funciones?|responsabilidades?|formaci[oó]n acad[eé]mica)\s*:?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^se requiere\s*:?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^deseable\s*:?\s*", "Deseable ", text, flags=re.IGNORECASE)

        if len(text) > 95:
            text = text[:92].rstrip() + "..."

        return text[:1].upper() + text[1:] if text else ""

    def _infer_category(self, item: str, section_name: str) -> str:
        lowered = item.lower()

        education_markers = [
            "formación", "formacion", "título", "titulo", "educación", "educacion",
            "ingeniero en", "ingeniera en", "técnico en", "tecnico en", "profesional en",
        ]

        if section_name == "education" or any(token in lowered for token in education_markers):
            return "formacion"
        if section_name == "experience" or any(token in lowered for token in ["experiencia", "años", "año", "trayectoria", "cargos similares"]):
            return "experiencia"
        if any(token in lowered for token in ["licencia", "certificación", "certificacion", "acreditación", "acreditacion", "permiso"]):
            return "requisito_formal"
        if any(token in lowered for token in ["terreno", "faena", "turno", "turnos", "viajar", "disponibilidad", "zona", "modalidad", "presencial"]):
            return "contextual"
        if any(token in lowered for token in ["comunicación", "comunicacion", "liderazgo", "equipo", "coordinación", "coordinacion", "cliente", "proveedor", "relacionamiento"]):
            return "transversal"
        return "tecnica"

    def _infer_importance(self, item: str, section_name: str) -> str:
        lowered = item.lower()
        if any(token in lowered for token in ["excluyente", "obligatorio", "requisito", "mínimo", "minimo", "al menos"]):
            return "alta"
        if section_name in {"requirements", "experience", "education"}:
            return "alta"
        if any(token in lowered for token in ["deseable", "idealmente", "valorable"]):
            return "media"
        return "media"

    def _initial_weight(self, category: str, importance: str) -> float:
        base = {
            "experiencia": 0.22,
            "tecnica": 0.20,
            "formacion": 0.18,
            "requisito_formal": 0.15,
            "contextual": 0.13,
            "transversal": 0.12,
        }.get(category, 0.15)

        factor = {"alta": 1.25, "media": 1.0, "baja": 0.75}.get(importance, 1.0)
        return round(base * factor, 4)

    def _expected_evidence(self, item: str, category: str) -> str:
        if category == "formacion":
            return "Título, formación académica, estudios, cursos o certificaciones relacionadas con el requisito del anuncio."
        if category == "experiencia":
            return "Cargos previos, años de experiencia, funciones realizadas o trayectoria relacionada con el requisito del anuncio."
        if category == "requisito_formal":
            return "Licencia, certificación, acreditación, permiso o requisito formal mencionado explícitamente en el CV."
        if category == "contextual":
            return "Experiencia, disponibilidad o funciones desarrolladas en el contexto operativo solicitado por el anuncio."
        if category == "transversal":
            return "Responsabilidades, logros o funciones que evidencien habilidades transversales solicitadas por el anuncio."
        return "Conocimientos, herramientas, funciones técnicas, metodologías o procedimientos relacionados con el requisito del anuncio."

    def _dict_to_competency(self, item: dict[str, Any]) -> Competency | None:
        name = str(item.get("name", "")).strip()
        if not name:
            return None

        category = str(item.get("category", "tecnica")).strip().lower()
        category_map = {
            "technical": "tecnica",
            "technique": "tecnica",
            "experience": "experiencia",
            "education": "formacion",
            "training": "formacion",
            "transversal": "transversal",
            "soft_skill": "transversal",
            "contextual": "contextual",
            "formal": "requisito_formal",
            "formal_requirement": "requisito_formal",
        }
        category = category_map.get(category, category)
        if category not in self.VALID_CATEGORIES:
            category = "tecnica"

        importance = str(item.get("importance", "media")).strip().lower()
        importance_map = {"high": "alta", "medium": "media", "low": "baja"}
        importance = importance_map.get(importance, importance)
        if importance not in self.VALID_IMPORTANCE:
            importance = "media"

        try:
            weight = float(item.get("weight", 0.1))
        except (TypeError, ValueError):
            weight = 0.1
        weight = max(0.01, min(weight, 1.0))

        return Competency(
            name=name[:120],
            category=category,  # type: ignore[arg-type]
            weight=weight,
            importance=importance,  # type: ignore[arg-type]
            expected_evidence=str(item.get("expected_evidence", "Evidencia documental relacionada en el CV.")).strip(),
            source_text=str(item.get("source_text", "")).strip() or None,
            reason=str(item.get("reason", "")).strip() or None,
        )

    def _deduplicate(self, competencies: list[Competency]) -> list[Competency]:
        seen: set[str] = set()
        unique: list[Competency] = []
        for comp in competencies:
            key = self._normalize_key(comp.name)
            if key and key not in seen:
                seen.add(key)
                unique.append(comp)
        return unique

    def _deduplicate_text(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for item in items:
            key = self._normalize_key(item)
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        return unique

    def _normalize_key(self, text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^a-záéíóúñü0-9 ]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_weights(self, competencies: list[Competency]) -> list[Competency]:
        if not competencies:
            return []

        total = sum(max(c.weight, 0.0) for c in competencies) or 1.0
        normalized: list[Competency] = []
        for comp in competencies:
            normalized.append(comp.model_copy(update={"weight": round(max(comp.weight, 0.0) / total, 4)}))

        # Correct rounding drift so the table adds to 100%.
        drift = round(1.0 - sum(c.weight for c in normalized), 4)
        if normalized and drift:
            normalized[0] = normalized[0].model_copy(update={"weight": round(normalized[0].weight + drift, 4)})

        return normalized

    def _clean_text(self, text: str) -> str:
        text = text or ""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
