"""Screen capture, entirely in memory.

There is deliberately no path in this module that writes a file: the grab comes
back as a ``PIL.Image`` and the crop is a plain ``crop()`` call on it.
"""

from __future__ import annotations

import io
import subprocess
import sys
import threading
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


def list_monitors() -> list[Monitor]:
    """Displays available to the capture path, in ScreenCaptureKit order."""
    if sys.platform == "darwin":
        try:
            found = _sck_monitors()
            if found:
                return found
        except CaptureError:
            pass  # older macOS: fall through to mss
    found = _mss_monitors()
    if not found:
        raise CaptureError("no display detected")
    return found


def primary_monitor() -> Monitor:
    """The display the overlay covers in v1."""
    return list_monitors()[0]


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


def _sck_content(timeout: float = 10.0) -> Any:
    """Ask ScreenCaptureKit for the shareable content, without touching disk."""
    try:
        import ScreenCaptureKit as SCK
    except ImportError as exc:  # pragma: no cover - guarded by the [mac] extra
        raise CaptureError(
            "ScreenCaptureKit is not available; run: uv sync --extra mac"
        ) from exc

    done = threading.Event()
    result: dict[str, Any] = {}

    def on_content(content: Any, error: Any) -> None:
        # A completion block must return None; returning anything from a Python
        # callback raises inside libobjc and can terminate the process.
        result["content"] = content
        result["error"] = error
        done.set()

    try:
        SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            False, True, on_content
        )
    except Exception as exc:  # noqa: BLE001 - report one actionable line
        raise CaptureError(f"cannot enumerate displays: {exc}") from exc
    if not done.wait(timeout):
        raise CaptureError("display enumeration timed out")
    if result.get("error") is not None:
        raise CaptureError(f"cannot enumerate displays: {result['error']}")
    return result["content"]


def _sck_monitors() -> list[Monitor]:
    displays = list(_sck_content().displays())
    return [
        Monitor(
            int(display.frame().origin.x),
            int(display.frame().origin.y),
            int(display.width()),
            int(display.height()),
        )
        for display in displays
    ]


def _grab_with_screencapturekit(monitor: Monitor) -> Image.Image:
    """Capture a display with Apple's ScreenCaptureKit, entirely in RAM."""
    # Quartz registers the CGImage type with pyobjc; importing it only after
    # the callback has run leaves CGImage as an opaque pointer and can crash.
    import Quartz  # noqa: F401
    import ScreenCaptureKit as SCK
    from AppKit import NSBitmapImageFileTypePNG, NSBitmapImageRep

    displays = list(_sck_content().displays())
    if not displays:
        raise CaptureError("no display detected")
    display = next(
        (
            item
            for item in displays
            if int(item.width()) == monitor.width and int(item.height()) == monitor.height
        ),
        displays[0],
    )

    scale = float(
        SCK.SCContentFilter.alloc().pointPixelScaleForDisplayID_(display.displayID())
    )
    if scale <= 0:
        scale = 1.0
    config = SCK.SCStreamConfiguration.alloc().init()
    config.setWidth_(max(1, int(round(monitor.width * scale))))
    config.setHeight_(max(1, int(round(monitor.height * scale))))
    content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
        display, []
    )

    done = threading.Event()
    result: dict[str, Any] = {}

    def on_image(image: Any, error: Any) -> None:
        result["image"] = image
        result["error"] = error
        done.set()

    try:
        SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
            content_filter, config, on_image
        )
    except Exception as exc:  # noqa: BLE001 - report one actionable line
        raise CaptureError(f"screen capture failed: {exc}") from exc
    if not done.wait(15):
        raise CaptureError("screen capture timed out")
    if result.get("error") is not None:
        raise CaptureError(f"screen capture failed: {result['error']}")
    cg_image = result.get("image")
    if cg_image is None:
        raise CaptureError("screen capture returned no image")

    try:
        bitmap = NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
        png = bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        with Image.open(io.BytesIO(bytes(png))) as opened:
            return opened.convert("RGB").copy()
    except Exception as exc:  # noqa: BLE001
        raise CaptureError(f"cannot convert the captured image: {exc}") from exc


def grab_desktop(monitor: Monitor | None = None) -> Image.Image:
    """Grab one display (the primary one by default) into a ``PIL.Image``."""
    target = monitor or primary_monitor()

    if sys.platform == "darwin":
        try:
            image = _grab_with_screencapturekit(target)
        except CaptureError:
            image = _grab_with_mss(target)  # older macOS / missing framework
        if is_blank(image):
            # A flat frame means permission denied or a locked display. The
            # CLI fallback is a second, independent native capture path.
            fallback = _grab_with_screencapture(target)
            if fallback is not None and not is_blank(fallback):
                return fallback
        return image

    return _grab_with_mss(target)


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
