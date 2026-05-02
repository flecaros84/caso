from pathlib import Path
import os
import re

import fitz  # PyMuPDF
from PIL import Image, ImageOps, ImageFilter

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None


class TextExtractor:
    def __init__(self) -> None:
        self._configure_tesseract_windows_path()

    def extract(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._from_pdf(path)
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return self._from_image(path)
        if suffix in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        raise ValueError(f"Formato no soportado: {suffix}")

    def _from_pdf(self, path: Path) -> str:
        chunks: list[str] = []
        with fitz.open(path) as doc:
            for page in doc:
                text = page.get_text("text")
                if text.strip():
                    chunks.append(text)
        return self._clean("\n".join(chunks))

    def _from_image(self, path: Path) -> str:
        if pytesseract is None:
            return ""

        try:
            image = Image.open(path)
            image = self._preprocess_for_ocr(image)

            # Try Spanish + English first. If Spanish language data is missing,
            # fallback to English/default OCR instead of failing completely.
            try:
                text = pytesseract.image_to_string(image, lang="spa+eng")
            except Exception:
                text = pytesseract.image_to_string(image)

            return self._clean(text)
        except Exception:
            return ""

    def _preprocess_for_ocr(self, image: Image.Image) -> Image.Image:
        # Convert to grayscale, enlarge, increase contrast and sharpen.
        image = ImageOps.grayscale(image)
        width, height = image.size
        image = image.resize((width * 2, height * 2))
        image = ImageOps.autocontrast(image)
        image = image.filter(ImageFilter.SHARPEN)
        return image

    def _configure_tesseract_windows_path(self) -> None:
        if pytesseract is None:
            return

        env_cmd = os.getenv("TESSERACT_CMD")
        candidates = [
            env_cmd,
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                pytesseract.pytesseract.tesseract_cmd = candidate
                return

    def _clean(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        return text.strip()
