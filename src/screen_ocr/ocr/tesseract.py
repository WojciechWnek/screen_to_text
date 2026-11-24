"""Tesseract fallback backend (optional extra).

Word-level boxes, so multi-column layouts reconstruct well - as long as the
binary is installed.
"""

from __future__ import annotations

from PIL import Image

from ..models import Token
from .base import OcrError, OcrUnavailable


class TesseractBackend:
    name = "tesseract"

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang or "eng"

    def read_tokens(self, image: Image.Image) -> list[Token]:
        try:
            import pytesseract
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise OcrUnavailable(
                "the Tesseract backend needs pytesseract: uv sync --extra tesseract"
            ) from exc

        try:
            data = pytesseract.image_to_data(
                image, lang=self.lang, output_type=pytesseract.Output.DICT
            )
        except pytesseract.TesseractNotFoundError as exc:  # pragma: no cover
            raise OcrUnavailable(
                "tesseract binary not found; install it with: brew install tesseract"
            ) from exc
        except pytesseract.TesseractError as exc:  # pragma: no cover
            raise OcrError(f"Tesseract failed: {exc}") from exc

        tokens: list[Token] = []
        for index, text in enumerate(data["text"]):
            if not str(text).strip():
                continue
            # Tesseract reports confidence as a percentage, and -1 for non-text.
            confidence = max(float(data["conf"][index]), 0.0) / 100.0
            tokens.append(
                Token(
                    text=str(text),
                    left=float(data["left"][index]),
                    top=float(data["top"][index]),
                    width=float(data["width"][index]),
                    height=float(data["height"][index]),
                    conf=confidence,
                )
            )
        return tokens
