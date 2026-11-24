"""OCR backend selection.

One small factory so nothing outside this package needs to know which engines
exist or which platform it is running on.
"""

from __future__ import annotations

import platform

from .base import OcrBackend, OcrError, OcrUnavailable

__all__ = ["OcrBackend", "OcrError", "OcrUnavailable", "create_backend", "platform_engine"]


def platform_engine() -> str:
    system = platform.system()
    if system == "Darwin":
        return "vision"
    if system == "Windows":
        return "windows"
    return "tesseract"


def create_backend(engine: str = "auto", lang: str | None = None) -> OcrBackend:
    choice = platform_engine() if engine == "auto" else engine

    if choice == "vision":
        from .vision import VisionBackend

        return VisionBackend(lang)

    if choice == "windows":
        from .windows import WindowsBackend

        return WindowsBackend(lang)

    if choice == "tesseract":
        from .tesseract import TesseractBackend

        return TesseractBackend(lang)

    raise OcrUnavailable(f"unknown OCR engine: {choice!r}")
