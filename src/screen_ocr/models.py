"""Data models shared between the OCR backends and the layout engine.

``Token`` is deliberately geometry-only: no engine-specific types leak past the
OCR boundary, which is what lets ``layout.py`` stay a pure function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Token:
    """A recognised piece of text and where it sits in the image.

    A token may be a single word (Windows OCR, Tesseract) or a whole
    phrase/line (macOS Vision). ``layout.py`` must not assume either.

    Coordinates are in image pixels, relative to the top-left corner of the
    captured region.
    """

    text: str
    left: float
    top: float
    width: float
    height: float
    conf: float = 1.0

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2.0

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Token:
        """Build a token from a JSON-ish mapping, ignoring unknown keys."""
        return cls(
            text=str(data["text"]),
            left=float(data["left"]),
            top=float(data["top"]),
            width=float(data["width"]),
            height=float(data["height"]),
            conf=float(data.get("conf", 1.0)),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
            "conf": self.conf,
        }
