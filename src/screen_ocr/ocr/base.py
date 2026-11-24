"""OCR backend interface.

Every engine returns plain :class:`~screen_ocr.models.Token` geometry, so the
layout engine never learns which engine ran.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from PIL import Image

from ..models import Token


class OcrError(RuntimeError):
    """Raised when recognition itself fails."""


class OcrUnavailable(OcrError):
    """Raised when the requested engine cannot run on this machine."""


@runtime_checkable
class OcrBackend(Protocol):
    name: str

    def read_tokens(self, image: Image.Image) -> list[Token]:
        """Recognise text and return it as positioned tokens."""
