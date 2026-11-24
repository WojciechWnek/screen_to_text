"""Native macOS selection overlay.

The desktop image is already in memory when this window opens. AppKit draws it
as a frozen full-screen preview; the pointer rectangle is mapped back to image
pixels by :class:`~screen_ocr.selection_state.SelectionState`. There is no Tk,
no Retina conversion in the crop path and no image written to disk.
"""

from __future__ import annotations

import io
from typing import Any

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBorderlessWindowMask,
    NSColor,
    NSCompositeSourceOver,
    NSCursor,
    NSEvent,
    NSEventMaskLeftMouseUp,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
    NSImage,
    NSMakePoint,
    NSMakeRect,
    NSObject,
    NSRectFill,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSScreen,
    NSTimer,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from PIL import Image
from Quartz import kCGStatusWindowLevel

from .capture import Box
from .overlay import OverlayError
from .selection_state import VEIL_ALPHA, SelectionState

_ESCAPE_KEYCODE = 53


class _AppDelegate(NSObject):  # type: ignore[misc]
    """Keep the CLI process alive when the overlay window closes."""

    def applicationShouldTerminateAfterLastWindowClosed_(self, _app: Any) -> bool:
        return False


class _WindowDelegate(NSObject):  # type: ignore[misc]
    def windowWillClose_(self, _notification: Any) -> None:
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.window_closed()


class _OverlayWindow(NSWindow):  # type: ignore[misc]
    """Borderless full-screen window that can receive key events."""

    def canBecomeKeyWindow(self) -> bool:
        return True

    def canBecomeMainWindow(self) -> bool:
        return True

    def acceptsFirstMouse_(self, _event: Any) -> bool:
        return True


class _SelectionView(NSView):  # type: ignore[misc]
    """Draws the frozen preview and forwards pointer/key events to its owner."""

    def isFlipped(self) -> bool:
        # All pointer coordinates then have a top-left origin, like the image.
        return True

    def acceptsFirstResponder(self) -> bool:
        return True

    def drawRect_(self, _rect: Any) -> None:
        _guard(self, lambda owner: owner.draw())

    def mouseDown_(self, event: Any) -> None:
        def handle(owner: MacSelectionOverlay) -> None:
            x, y = _point(event, self)
            if event.buttonNumber() == 1:
                owner.cancel()
            else:
                owner.pointer_down(x, y)

        _guard(self, handle)

    def mouseDragged_(self, event: Any) -> None:
        _guard(
            self, lambda owner: owner.pointer_move(*_point(event, self))
        )

    def mouseUp_(self, event: Any) -> None:
        _guard(self, lambda owner: owner.pointer_up(*_point(event, self)))

    def rightMouseDown_(self, _event: Any) -> None:
        _guard(self, lambda owner: owner.cancel())

    def keyDown_(self, event: Any) -> None:
        def handle(owner: MacSelectionOverlay) -> None:
            if event.keyCode() == _ESCAPE_KEYCODE or "\x1b" in (
                event.characters() or ""
            ):
                owner.cancel()

        _guard(self, handle)


def _guard(view: Any, action: Any) -> None:
    """Never leave the modal loop running after an event-handler error."""
    owner = getattr(view, "owner", None)
    if owner is None:  # teardown can redraw after the owner has been cleared
        return
    try:
        action(owner)
    except Exception as exc:  # noqa: BLE001 - must not trap the user
        owner.fail(exc)


def _point(event: Any, view: Any) -> tuple[float, float]:
    """Convert an event to top-left view points.

    PyObjC 13 does not expose ``-[NSEvent locationInView:]``. Passing
    ``locationInWindow`` through ``convertPoint:fromView:`` already accounts for
    the flipped view, so do **not** flip y a second time.
    """
    local = view.convertPoint_fromView_(event.locationInWindow(), None)
    return float(local.x), float(local.y)


def _ns_image(image: Image.Image) -> Any:
    """Convert a PIL image to NSImage through an in-memory PNG buffer."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return NSImage.alloc().initWithData_(buffer.getvalue())


def schedule(delay: float, callback: Any) -> Any:
    """Run ``callback`` during a modal overlay loop.

    ``scheduledTimer...`` installs the timer only in the default run-loop mode,
    but ``runModalForWindow`` switches to the modal mode. Common modes cover
    both, which is why this helper exists.
    """
    timer = NSTimer.timerWithTimeInterval_repeats_block_(delay, False, callback)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
    return timer


class MacSelectionOverlay:
    """AppKit overlay returning a crop box in captured-image pixels."""

    def __init__(
        self, image: Image.Image, *, auto_close_ms: int | None = None
    ) -> None:
        self._image = image.convert("RGB")
        self._auto_close_ms = auto_close_ms
        self._app: Any | None = None
        self._app_delegate: Any | None = None
        self._window: Any | None = None
        self._window_delegate: Any | None = None
        self._view: Any | None = None
        self._timer: Any | None = None
        self._mouse_monitor: Any | None = None
        self._modal = False
        self._finished = False
        self._event_error: Exception | None = None
        self._box: Box | None = None
        self._viewport = (0, 0)
        self._state = SelectionState(self._image.size, (1, 1))
        self._bright: Any | None = None
        self._dim: Any | None = None

    # -- public API ------------------------------------------------------- #

    def run(self) -> Box | None:
        """Show the overlay on the main thread; None means cancelled."""
        if self._finished:
            return self._box

        screen = NSScreen.mainScreen()
        if screen is None:
            raise OverlayError("no display available for selection")
        frame = screen.frame()
        width = max(1, int(round(frame.size.width)))
        height = max(1, int(round(frame.size.height)))
        self._viewport = (width, height)
        self._state = SelectionState(self._image.size, self._viewport)

        try:
            self._app = NSApplication.sharedApplication()
            if self._app.delegate() is None:
                self._app_delegate = _AppDelegate.alloc().init()
                self._app.setDelegate_(self._app_delegate)
            try:
                self._app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            except Exception:  # noqa: BLE001 - already launching / bundled
                pass
            self._build(frame, width, height)
            self._app.activateIgnoringOtherApps_(True)
            self._schedule_auto_close()

            self._modal = True
            self._app.runModalForWindow_(self._window)
            if self._event_error is not None:
                raise OverlayError(
                    f"selection window failed: {self._event_error}"
                ) from self._event_error
        except OverlayError:
            self._box = None
            raise
        except Exception as exc:  # noqa: BLE001 - one actionable line from the CLI
            self._box = None
            raise OverlayError(f"cannot open the selection window: {exc}") from exc
        finally:
            self._destroy()
        return self._box

    # -- construction ------------------------------------------------------ #

    def _build(self, frame: Any, width: int, height: int) -> None:
        dimmed = Image.blend(
            self._image, Image.new("RGB", self._image.size, "black"), VEIL_ALPHA
        )
        self._bright = _ns_image(self._image)
        self._dim = _ns_image(dimmed)

        window = _OverlayWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSBorderlessWindowMask, NSBackingStoreBuffered, False
        )
        # High enough to cover normal windows, low enough that system dialogs
        # and Force Quit remain reachable if something goes wrong.
        window.setLevel_(kCGStatusWindowLevel)
        window.setOpaque_(True)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )

        view = _SelectionView.alloc().initWithFrame_(
            NSMakeRect(0, 0, float(width), float(height))
        )
        view.owner = self
        window.setContentView_(view)

        delegate = _WindowDelegate.alloc().init()
        delegate.owner = self
        window.setDelegate_(delegate)

        self._window = window
        self._view = view
        self._window_delegate = delegate
        # A release outside our full-screen window never reaches the view. A
        # global monitor turns that into a cancel instead of a stuck overlay.
        self._mouse_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskLeftMouseUp, self._outside_mouse_up
        )
        NSCursor.crosshairCursor().set()

    def _outside_mouse_up(self, event: Any) -> None:
        try:
            if self._finished or not self._state.active:
                return
            if (
                self._window is not None
                and event.windowNumber() == self._window.windowNumber()
            ):
                return  # the view handles this one
            self.cancel()
        except Exception as exc:  # noqa: BLE001 - must not trap the user
            self.fail(exc)

    def _schedule_auto_close(self) -> None:
        if self._auto_close_ms is None:
            return
        delay = max(1, self._auto_close_ms) / 1000.0

        def close(_timer: Any) -> None:
            self.cancel()

        self._timer = schedule(delay, close)

    # -- pointer/key bridge ------------------------------------------------ #

    def pointer_down(self, x: float, y: float) -> None:
        if self._finished:
            return
        self._state.down(x, y)
        self._redraw()

    def pointer_move(self, x: float, y: float) -> None:
        if self._finished or not self._state.active:
            return
        self._state.drag(x, y)
        self._redraw()

    def pointer_up(self, x: float, y: float) -> None:
        if self._finished or not self._state.active:
            return
        self._finish(self._state.up(x, y))

    def cancel(self) -> None:
        if self._finished:
            return
        self._state.cancel()
        self._finish(None)

    def fail(self, exc: Exception) -> None:
        """Cancel and report an event-handler failure after the loop exits."""
        self._event_error = exc
        self.cancel()

    def window_closed(self) -> None:
        """Window-close is a cancel, even if no pointer event arrived."""
        self.cancel()

    # -- drawing ----------------------------------------------------------- #

    def _redraw(self) -> None:
        if self._view is not None:
            self._view.setNeedsDisplay_(True)

    def draw(self) -> None:
        """Draw the frozen preview, veil, outline and size label."""
        if NSGraphicsContext.currentContext() is None:
            return
        width, height = self._viewport
        target = NSMakeRect(0, 0, float(width), float(height))
        source = NSMakeRect(
            0, 0, float(self._image.width), float(self._image.height)
        )
        NSGraphicsContext.saveGraphicsState()
        try:
            if self._dim is not None:
                self._dim.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                    target, source, NSCompositeSourceOver, 1.0, True, None
                )

            selection = self._state.selection_points()
            if selection is None:
                return
            left, top, right, bottom = selection

            NSGraphicsContext.saveGraphicsState()
            try:
                NSBezierPath.bezierPathWithRect_(
                    NSMakeRect(left, top, right - left, bottom - top)
                ).setClip()
                if self._bright is not None:
                    self._bright.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                        target, source, NSCompositeSourceOver, 1.0, True, None
                    )
            finally:
                NSGraphicsContext.restoreGraphicsState()

            NSColor.colorWithCalibratedWhite_alpha_(0.0, VEIL_ALPHA).setFill()
            for x1, y1, x2, y2 in self._state.veil_rects():
                NSRectFill(NSMakeRect(x1, y1, x2 - x1, y2 - y1))

            outline = NSBezierPath.bezierPathWithRect_(
                NSMakeRect(left, top, right - left, bottom - top)
            )
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.83, 1.0, 1.0).setStroke()
            outline.setLineWidth_(2.0)
            outline.stroke()

            box = self._state.image_box
            if box is not None:
                self._draw_label(
                    f"{box[2] - box[0]} x {box[3] - box[1]}",
                    left,
                    top,
                    width,
                    height,
                )
        finally:
            NSGraphicsContext.restoreGraphicsState()

    def _draw_label(
        self, text: str, left: float, top: float, width: int, height: int
    ) -> None:
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(12.0),
            NSForegroundColorAttributeName: NSColor.colorWithCalibratedWhite_alpha_(
                0.9, 1.0
            ),
        }
        label = NSAttributedString.alloc().initWithString_attributes_(text, attributes)
        size = label.size()
        x = min(left + 8, max(width - size.width - 12, 0))
        y = min(top + 8, max(height - size.height - 12, 0))
        background = NSMakeRect(x - 4, y - 2, size.width + 8, size.height + 4)
        NSColor.colorWithCalibratedWhite_alpha_(0.06, 0.92).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            background, 4.0, 4.0
        ).fill()
        label.drawAtPoint_(NSMakePoint(x, y))

    # -- teardown ---------------------------------------------------------- #

    def _finish(self, box: Box | None) -> None:
        if self._finished:
            return
        self._finished = True
        self._box = box
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._mouse_monitor is not None:
            NSEvent.removeMonitor_(self._mouse_monitor)
            self._mouse_monitor = None
        if self._modal and self._app is not None:
            self._modal = False
            self._app.stopModalWithCode_(0)
        if self._window is not None:
            self._window.close()

    def _destroy(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._mouse_monitor is not None:
            NSEvent.removeMonitor_(self._mouse_monitor)
            self._mouse_monitor = None
        NSCursor.arrowCursor().set()
        if self._window is not None:
            try:
                self._window.close()
            except Exception:  # noqa: BLE001 - teardown must not mask the result
                pass
        if self._window_delegate is not None:
            self._window_delegate.owner = None
        if self._view is not None:
            self._view.owner = None
        self._window = None
        self._view = None
        self._window_delegate = None
        self._bright = None
        self._dim = None
