"""Windows backend using the OS OCR engine (``Windows.Media.Ocr``).

Advantage over the macOS path: this API returns *word* level bounding boxes, so
multi-column layouts reconstruct more faithfully.

Status: written but **unverified** - there is no Windows machine in this repo's
development loop yet. Every failure is wrapped in ``OcrUnavailable`` with a hint
to fall back to ``--engine tesseract`` so it degrades instead of crashing.
"""

from __future__ import annotations

import asyncio
import io

from PIL import Image

from ..models import Token
from .base import OcrError, OcrUnavailable


class WindowsBackend:
    name = "windows"

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang

    def read_tokens(self, image: Image.Image) -> list[Token]:
        try:
            import winsdk  # noqa: F401
        except ImportError as exc:  # pragma: no cover - platform specific
            raise OcrUnavailable(
                "the Windows OCR backend needs winsdk: uv sync --extra win"
            ) from exc

        try:
            return asyncio.run(self._read(image))
        except OcrUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - unverified path, degrade cleanly
            raise OcrUnavailable(
                f"Windows OCR failed ({exc}); try: stt grab --engine tesseract"
            ) from exc

    async def _read(self, image: Image.Image) -> list[Token]:
        from winsdk.windows.globalization import Language
        from winsdk.windows.media.ocr import OcrEngine

        engine = self._create_engine(OcrEngine, Language)
        if engine is None:
            raise OcrUnavailable(
                "no Windows OCR language pack is installed; add one in "
                "Settings > Time & language > Language & region"
            )

        bitmap = await _software_bitmap(image)
        result = await engine.recognize_async(bitmap)

        tokens: list[Token] = []
        for line in result.lines:
            for word in line.words:
                text = str(word.text)
                if not text.strip():
                    continue
                rect = word.bounding_rect
                tokens.append(
                    Token(
                        text=text,
                        left=float(rect.x),
                        top=float(rect.y),
                        width=float(rect.width),
                        height=float(rect.height),
                        conf=1.0,
                    )
                )
        return tokens

    def _create_engine(self, ocr_engine: object, language: object) -> object | None:
        if self.lang:
            engine = ocr_engine.try_create_from_language(language(self.lang))  # type: ignore[attr-defined]
            if engine is not None:
                return engine
        return ocr_engine.try_create_from_user_profile_languages()  # type: ignore[attr-defined]


async def _software_bitmap(image: Image.Image) -> object:
    """PNG bytes -> SoftwareBitmap, entirely in memory."""
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(buffer.getvalue())
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)

    decoder = await BitmapDecoder.create_async(stream)
    return await decoder.get_software_bitmap_async()


__all__ = ["WindowsBackend", "OcrError"]
