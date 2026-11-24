"""macOS Vision backend.

Two details matter more than anything else in this file:

* ``setUsesLanguageCorrection_(False)`` - language correction rewrites code
  (``def foo ()``, ``self . x``, ``x == 1`` becomes ``x = 1``).
* Vision boxes are normalised with the origin at the *bottom left*, while
  everything downstream expects top-left pixels.
"""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from ..models import Token
from .base import OcrError, OcrUnavailable

NormalizedBox = tuple[float, float, float, float]


class VisionBackend:
    name = "vision"

    def __init__(self, lang: str | None = None) -> None:
        self.lang = lang

    def read_tokens(self, image: Image.Image) -> list[Token]:
        try:
            import Vision
            from Foundation import NSData
        except ImportError as exc:  # pragma: no cover - platform specific
            raise OcrUnavailable(
                "the Vision backend needs pyobjc: uv sync --extra mac"
            ) from exc

        width, height = image.size
        payload = _png_bytes(image)
        data = NSData.dataWithBytes_length_(payload, len(payload))

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        # Never let the language model "fix" code.
        request.setUsesLanguageCorrection_(False)
        if self.lang:
            request.setRecognitionLanguages_([self.lang])

        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        outcome = handler.performRequests_error_([request], None)
        ok, error = outcome if isinstance(outcome, tuple) else (bool(outcome), None)
        if not ok:
            raise OcrError(f"Vision failed: {error}")

        tokens: list[Token] = []
        for observation in request.results() or []:
            token = _observation_to_token(observation, width, height)
            if token is not None:
                tokens.append(token)
        return tokens


def normalized_box_to_pixels(
    box: NormalizedBox, width: int, height: int
) -> tuple[float, float, float, float]:
    """Vision's bottom-left normalised box -> top-left pixel box."""
    x, y, box_width, box_height = box
    # max(0.0, ...) absorbs float noise: 1.0 - 0.9 - 0.1 is -2.7e-15, not 0.
    return (
        max(0.0, x * width),
        max(0.0, (1.0 - y - box_height) * height),
        max(0.0, box_width * width),
        max(0.0, box_height * height),
    )


def _observation_to_token(
    observation: Any, width: int, height: int
) -> Token | None:
    candidates = observation.topCandidates_(1)
    if not candidates:
        return None

    candidate = candidates[0]
    text = str(candidate.string())
    if not text.strip():
        return None

    rect = observation.boundingBox()
    left, top, box_width, box_height = normalized_box_to_pixels(
        (
            float(rect.origin.x),
            float(rect.origin.y),
            float(rect.size.width),
            float(rect.size.height),
        ),
        width,
        height,
    )
    return Token(
        text=text,
        left=left,
        top=top,
        width=box_width,
        height=box_height,
        conf=float(candidate.confidence()),
    )


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
