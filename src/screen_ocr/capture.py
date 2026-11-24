"""Screen capture, entirely in memory.

There is deliberately no path in this module that writes a file: the grab comes
back as a ``PIL.Image`` and the crop is a plain ``crop()`` call on it.
"""

from __future__ import annotations

import io
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from PIL import Image

Box = tuple[int, int, int, int]

#: Uniformity threshold for :func:`is_blank` (0-255 grayscale range).
BLANK_RANGE = 4

PERMISSION_HINT = (
    "screen capture returned a blank image. Either macOS is blocking it, or the\n"
    "display is asleep/locked.\n"
    "Grant Screen Recording to your terminal/app, then quit and reopen it:\n"
    "  System Settings > Privacy & Security > Screen Recording\n"
    "macOS only applies the permission to newly started processes."
)


class CaptureError(RuntimeError):
    """Raised when the desktop cannot be grabbed at all."""


@dataclass(frozen=True, slots=True)
class Monitor:
    left: int
    top: int
    width: int
    height: int

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


def primary_monitor() -> Monitor:
    """The display the overlay covers in v1."""
    monitors = _mss_monitors()
    if not monitors:
        raise CaptureError("no display detected")
    return monitors[0]


def _mss_monitors() -> list[Monitor]:
    try:
        from mss import MSS
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CaptureError("mss is not installed; run: uv sync --extra mac") from exc

    try:
        with MSS() as sct:
            # index 0 is the union of all displays; the rest are physical ones
            raw = list(sct.monitors)[1:]
    except Exception as exc:  # noqa: BLE001 - surface a single clear message
        raise CaptureError(f"cannot enumerate displays: {exc}") from exc

    return [
        Monitor(
            left=int(monitor["left"]),
            top=int(monitor["top"]),
            width=int(monitor["width"]),
            height=int(monitor["height"]),
        )
        for monitor in raw
    ]


def grab_desktop(monitor: Monitor | None = None) -> Image.Image:
    """Grab one display (the primary one by default) into a ``PIL.Image``."""
    target = monitor or primary_monitor()

    image = _grab_with_mss(target)
    if is_blank(image) and sys.platform == "darwin":
        # Either the permission is missing or this macOS build broke mss'
        # deprecated CGWindowListCreateImage path. One fallback tells them apart.
        fallback = _grab_with_screencapture(target)
        if fallback is not None and not is_blank(fallback):
            return fallback
    return image


def _grab_with_mss(monitor: Monitor) -> Image.Image:
    try:
        from mss import MSS
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise CaptureError("mss is not installed; run: uv sync --extra mac") from exc

    try:
        with MSS() as sct:
            shot = sct.grab((monitor.left, monitor.top, monitor.width, monitor.height))
    except Exception as exc:  # noqa: BLE001
        raise CaptureError(f"screen capture failed: {exc}") from exc

    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _grab_with_screencapture(monitor: Monitor) -> Image.Image | None:
    """Fallback: pipe a PNG through stdout. Never touches the disk."""
    if sys.platform != "darwin":
        return None

    rectangle = f"{monitor.left},{monitor.top},{monitor.width},{monitor.height}"
    try:
        result = subprocess.run(
            ["screencapture", "-x", "-t", "png", "-R", rectangle, "-"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0 or not result.stdout:
        return None
    try:
        with Image.open(io.BytesIO(result.stdout)) as opened:
            return opened.convert("RGB").copy()
    except OSError:
        return None


def crop(image: Image.Image, box: Box) -> Image.Image:
    """Crop 1:1: the result's size is the box's size, never scaled."""
    left, top, right, bottom = normalize_box(box, image.size)
    return image.crop((left, top, right, bottom))


def normalize_box(box: Any, size: tuple[int, int]) -> Box:
    """Order, round and clamp a box to the image bounds."""
    width, height = size
    x1, y1, x2, y2 = (float(value) for value in box)
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return (
        max(0, min(int(round(left)), width)),
        max(0, min(int(round(top)), height)),
        max(0, min(int(round(right)), width)),
        max(0, min(int(round(bottom)), height)),
    )


def is_blank(image: Image.Image) -> bool:
    """True when the frame is a single flat colour (permission denied)."""
    gray = image.convert("L")
    if gray.width > 64 or gray.height > 64:
        gray = gray.resize((64, 64), Image.Resampling.BILINEAR)
    low, high = gray.getextrema()
    return high - low <= BLANK_RANGE


def permission_ok() -> bool:
    """Best-effort check used by ``stt doctor``. Triggers the TCC prompt once."""
    try:
        return not is_blank(grab_desktop())
    except CaptureError:
        return False
