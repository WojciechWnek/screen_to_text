"""Native macOS overlay tests.

The window is real and briefly visible; the drag is scripted on the modal run
loop. This is the shipping overlay on macOS, so it checks the same coordinate
contract as the Tk suite without ever calling into Tk.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

AppKit = pytest.importorskip("AppKit", reason="AppKit is macOS-only")
Quartz = pytest.importorskip("Quartz", reason="Quartz is macOS-only")

from PIL import Image  # noqa: E402

from screen_ocr.demo import SAMPLE_CODE, render_code  # noqa: E402
from screen_ocr.main import EXIT_OK, main  # noqa: E402
from screen_ocr.overlay import OverlayError  # noqa: E402
from screen_ocr.overlay_macos import MacSelectionOverlay, _point, schedule  # noqa: E402


def _screen_size() -> tuple[int, int]:
    screen = AppKit.NSScreen.mainScreen()
    if screen is None:
        raise RuntimeError("no main display")
    frame = screen.frame()
    return int(round(frame.size.width)), int(round(frame.size.height))


def _display_available() -> bool:
    try:
        _screen_size()
    except Exception:  # noqa: BLE001 - headless CI
        return False
    return True


pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not _display_available(),
    reason="needs macOS and a display",
)


def test_event_point_uses_views_own_flipped_conversion() -> None:
    class View:
        def convertPoint_fromView_(self, point: Any, _view: Any) -> Any:
            # NSView already flips y for a flipped view; trust its result.
            return SimpleNamespace(x=point.x, y=12.0)

    event = SimpleNamespace(locationInWindow=lambda: SimpleNamespace(x=12.0, y=88.0))

    assert _point(event, View()) == (12.0, 12.0)


def test_initial_draw_is_transparent(monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = MacSelectionOverlay(_image())
    overlay._viewport = _screen_size()
    fills: list[str] = []
    outlines: list[Any] = []

    class FakeFill:
        def __init__(self, name: str) -> None:
            self.name = name

        def setFill(self) -> None:
            fills.append(self.name)

    class FakeColor:
        @staticmethod
        def clearColor() -> FakeFill:
            return FakeFill("clear")

    class FakePath:
        @staticmethod
        def bezierPathWithRect_(rect: Any) -> Any:
            outlines.append(rect)
            return FakePath()

        def setLineWidth_(self, _width: float) -> None:
            pass

        def stroke(self) -> None:
            pass

    class FakeGraphicsContext:
        @staticmethod
        def currentContext() -> object:
            return object()

        @staticmethod
        def saveGraphicsState() -> None:
            pass

        @staticmethod
        def restoreGraphicsState() -> None:
            pass

    monkeypatch.setattr("screen_ocr.overlay_macos.NSColor", FakeColor)
    monkeypatch.setattr("screen_ocr.overlay_macos.NSBezierPath", FakePath)
    monkeypatch.setattr(
        "screen_ocr.overlay_macos.NSGraphicsContext", FakeGraphicsContext
    )
    monkeypatch.setattr(
        "screen_ocr.overlay_macos.NSRectFill", lambda _rect: None
    )

    overlay.draw()
    assert fills == ["clear"]  # only a transparent clear, no veil
    assert outlines == []  # no outline before the first drag


class _OutsideEvent:
    """Stands in for a release over another window/display."""

    def windowNumber(self) -> int:
        return -1


class ScriptedNativeOverlay(MacSelectionOverlay):
    """Drives the native overlay through its pointer/cancel bridge."""

    def __init__(
        self,
        image: Image.Image,
        drag: tuple[int, int, int, int] | None = None,
        *,
        escape: bool = False,
        close: bool = False,
        outside: bool = False,
    ) -> None:
        super().__init__(image)
        self._drag = drag
        self._escape = escape
        self._close = close
        self._outside = outside

    def _schedule_auto_close(self) -> None:
        if self._escape:
            schedule(0.05, lambda _timer: self.cancel())
            return
        if self._close:
            schedule(0.05, lambda _timer: self.window_closed())
            return
        if self._outside:
            schedule(0.05, lambda _timer: self.pointer_down(10, 10))
            schedule(0.10, lambda _timer: self._outside_mouse_up(_OutsideEvent()))
            return
        if self._drag is None:
            schedule(0.05, lambda _timer: self.cancel())
            return
        x1, y1, x2, y2 = self._drag
        schedule(0.05, lambda _timer: self.pointer_down(x1, y1))
        schedule(0.10, lambda _timer: self.pointer_move(x2, y2))
        schedule(0.15, lambda _timer: self.pointer_up(x2, y2))


def _image(scale: int = 1) -> Image.Image:
    width, height = _screen_size()
    return Image.new("RGB", (width * scale, height * scale), "white")


def test_native_selection_maps_one_to_one() -> None:
    assert ScriptedNativeOverlay(_image(), (100, 100, 400, 300)).run() == (
        100,
        100,
        400,
        300,
    )


def test_native_selection_scales_to_image_pixels() -> None:
    assert ScriptedNativeOverlay(_image(2), (100, 100, 400, 300)).run() == (
        200,
        200,
        800,
        600,
    )


def test_native_selection_is_clamped_to_image() -> None:
    width, height = _screen_size()
    box = ScriptedNativeOverlay(
        _image(), (10, 10, width + 500, height + 500)
    ).run()

    assert box is not None
    assert box[2] == width
    assert box[3] == height


def test_native_tiny_drag_cancels() -> None:
    assert ScriptedNativeOverlay(_image(), (50, 50, 51, 51)).run() is None


def test_native_escape_cancels() -> None:
    assert ScriptedNativeOverlay(_image(), escape=True).run() is None


def test_native_window_close_cancels() -> None:
    assert ScriptedNativeOverlay(_image(), close=True).run() is None


def test_native_release_outside_cancels() -> None:
    assert ScriptedNativeOverlay(_image(), outside=True).run() is None


def test_native_auto_close_cancels() -> None:
    assert MacSelectionOverlay(_image(), auto_close_ms=10).run() is None


def test_native_real_nsevents_map_to_image_pixels() -> None:
    class RealEvents(MacSelectionOverlay):
        def _schedule_auto_close(self) -> None:
            schedule(0.05, lambda _timer: self._play())

        def _play(self) -> None:
            # CGEvent locations use top-left display coordinates; NSEvent and
            # NSView then do their own flips before our handler sees the point.
            events = (
                (Quartz.kCGEventLeftMouseDown, "mouseDown_", 100, 100),
                (Quartz.kCGEventLeftMouseDragged, "mouseDragged_", 400, 300),
                (Quartz.kCGEventLeftMouseUp, "mouseUp_", 400, 300),
            )
            for kind, selector, x, y in events:
                cg = Quartz.CGEventCreateMouseEvent(
                    None, kind, Quartz.CGPointMake(x, y), Quartz.kCGMouseButtonLeft
                )
                getattr(self._view, selector)(AppKit.NSEvent.eventWithCGEvent_(cg))

    assert RealEvents(_image(2)).run() == (200, 200, 800, 600)


def test_native_event_failure_reports_and_does_not_hang() -> None:
    class Failing(MacSelectionOverlay):
        def _schedule_auto_close(self) -> None:
            schedule(0.05, lambda _timer: self.fail(RuntimeError("boom")))

    with pytest.raises(OverlayError, match="boom"):
        Failing(_image()).run()


def test_factory_uses_native_overlay_on_macos() -> None:
    from screen_ocr.overlay import SelectionOverlay

    overlay: Any = SelectionOverlay(_image())
    assert isinstance(overlay, MacSelectionOverlay)


def test_native_full_pipeline_copies_the_selected_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    width, height = _screen_size()
    scale = 2  # what ScreenCaptureKit returns for a Retina display
    snippet = render_code()
    offset = (120, 120)
    desktop = Image.new("RGB", (width * scale, height * scale), "#101418")
    desktop.paste(snippet, (offset[0] * scale, offset[1] * scale))
    drag = (
        offset[0] - 6,
        offset[1] - 6,
        min(int(round(offset[0] + (snippet.width + 12) / scale)), width),
        min(int(round(offset[1] + (snippet.height + 12) / scale)), height),
    )
    copied: dict[str, str] = {}

    monkeypatch.setattr("screen_ocr.main.grab_desktop", lambda: desktop)
    monkeypatch.setattr("screen_ocr.main.is_blank", lambda _image: False)
    monkeypatch.setattr(
        "screen_ocr.overlay.SelectionOverlay",
        lambda image, **_kwargs: ScriptedNativeOverlay(image, drag),
    )

    def fake_copy(text: str) -> bool:
        copied["text"] = text
        return True

    monkeypatch.setattr("screen_ocr.main.copy_to_clipboard", fake_copy)

    assert main(["grab"]) == EXIT_OK
    assert copied["text"] == SAMPLE_CODE.rstrip("\n")
