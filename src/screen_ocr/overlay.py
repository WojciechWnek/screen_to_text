"""Selection overlay API.

macOS uses a native AppKit/Quartz window (:mod:`screen_ocr.overlay_macos`).
Windows keeps the Tk implementation below. Both show the *already captured*
desktop, so the rectangle returned by :meth:`run` is in captured-image pixels
and the caller never has to convert between points and pixels.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol

from PIL import Image

from .capture import Box
from .selection_state import MIN_SELECTION_PX, VEIL_ALPHA

OUTLINE_COLOR = "#00d4ff"
LABEL_BACKGROUND = "#101418"
LABEL_FOREGROUND = "#e6f7ff"

__all__ = [
    "MIN_SELECTION_PX",
    "VEIL_ALPHA",
    "OverlayError",
    "SelectionOverlay",
    "TkSelectionOverlay",
    "select",
]


class OverlayError(RuntimeError):
    """Raised when the selection window cannot be created."""


class _Overlay(Protocol):
    def run(self) -> Box | None: ...


def SelectionOverlay(
    image: Image.Image, *, auto_close_ms: int | None = None
) -> _Overlay:
    """Return the platform's overlay implementation for ``image``.

    Kept as a callable with the old class name so the CLI wiring and its tests
    stay simple: ``SelectionOverlay(image).run()`` reads the same on both OSes.
    """
    if sys.platform == "darwin":
        from .overlay_macos import MacSelectionOverlay

        return MacSelectionOverlay(image, auto_close_ms=auto_close_ms)
    return TkSelectionOverlay(image, auto_close_ms=auto_close_ms)


def select(image: Image.Image, *, auto_close_ms: int | None = None) -> Box | None:
    """Convenience wrapper: build the overlay and run it once."""
    return SelectionOverlay(image, auto_close_ms=auto_close_ms).run()


def _tk_modules() -> tuple[Any, Any]:
    """Import Tk lazily; macOS must not depend on it at all."""
    try:
        import tkinter as tk
        from PIL import ImageTk
    except ImportError as exc:  # pragma: no cover - headless/minimal installs
        raise OverlayError(f"tkinter is not available: {exc}") from exc
    return tk, ImageTk


class TkSelectionOverlay:
    """Windows overlay: fullscreen Tk canvas over a frozen desktop image."""

    def __init__(self, image: Image.Image, *, auto_close_ms: int | None = None) -> None:
        self._image = image
        self._auto_close_ms = auto_close_ms
        self._tk: Any | None = None
        self._image_tk: Any | None = None
        self._root: Any | None = None
        self._canvas: Any | None = None
        self._image_item: int | None = None
        self._veil: list[int] = []
        self._outline: int | None = None
        self._label: int | None = None
        self._label_bg: int | None = None
        self._photos: tuple[Any, ...] = ()
        self._screen = (0, 0)
        self._scale = 1.0
        self._start: tuple[int, int] | None = None
        self._box: Box | None = None

    # -- public API ------------------------------------------------------- #

    def run(self) -> Box | None:
        """Show the overlay and return the selection in image pixels, or None."""
        tk, image_tk = _tk_modules()
        self._tk, self._image_tk = tk, image_tk
        root = tk.Tk()
        self._root = root
        try:
            self._build(root)
            root.mainloop()
        except tk.TclError as exc:  # pragma: no cover - no display available
            raise OverlayError(f"cannot open the selection window: {exc}") from exc
        finally:
            self._destroy()
        return self._box

    # -- construction ------------------------------------------------------ #

    def _build(self, root: Any) -> None:
        # Read the screen size before entering fullscreen. On macOS the native
        # fullscreen viewport excludes the menu bar; the preview is still drawn
        # at capture-pixel size from (0, 0), so the visible part stays 1:1.
        width, height = root.winfo_screenwidth(), root.winfo_screenheight()
        self._screen = (width, height)

        # Do NOT use overrideredirect here. On macOS/Tk 9 a borderless topmost
        # window that is merely sized to the screen renders blank white. Native
        # fullscreen is stable and draws the PhotoImage correctly.
        root.attributes("-topmost", True)
        root.attributes("-fullscreen", True)
        root.configure(bg="black")

        bright = self._image
        if bright.size != (width, height):
            bright = bright.resize((width, height), Image.Resampling.LANCZOS)
        self._scale = self._image.width / width if width else 1.0
        dimmed = Image.blend(
            bright, Image.new("RGB", bright.size, "black"), VEIL_ALPHA
        )
        assert self._image_tk is not None
        self._photos = (
            self._image_tk.PhotoImage(bright),
            self._image_tk.PhotoImage(dimmed),
        )

        tk = self._tk
        assert tk is not None
        canvas = tk.Canvas(
            root,
            width=width,
            height=height,
            highlightthickness=0,
            borderwidth=0,
            bg="black",
            cursor="crosshair",
        )
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas

        self._image_item = canvas.create_image(
            0, 0, anchor="nw", image=self._photos[1]
        )
        for _ in range(4):
            self._veil.append(
                canvas.create_rectangle(
                    0, 0, 0, 0, fill="black", outline="", state="hidden"
                )
            )
        self._outline = canvas.create_rectangle(
            0, 0, 0, 0, outline=OUTLINE_COLOR, width=2, state="hidden"
        )
        self._label_bg = canvas.create_rectangle(
            0, 0, 0, 0, fill=LABEL_BACKGROUND, outline="", state="hidden"
        )
        self._label = canvas.create_text(
            0,
            0,
            anchor="nw",
            fill=LABEL_FOREGROUND,
            font=("Menlo", 11),
            text="",
            state="hidden",
        )

        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        root.bind("<Escape>", self._on_cancel)
        root.bind("<Button-2>", self._on_cancel)
        root.bind("<Button-3>", self._on_cancel)
        root.protocol("WM_DELETE_WINDOW", self._on_cancel)

        root.lift()
        root.focus_force()
        if self._auto_close_ms is not None:
            root.after(self._auto_close_ms, self._on_cancel)

    # -- interaction ------------------------------------------------------- #

    def _on_press(self, event: Any) -> None:
        if self._canvas is None or self._image_item is None:
            return
        self._start = (event.x, event.y)
        # From here on the selected area is shown undimmed: bright image on the
        # canvas, solid black panels covering everything outside the rectangle.
        self._canvas.itemconfigure(self._image_item, image=self._photos[0])
        self._paint(event.x, event.y, event.x, event.y)

    def _on_drag(self, event: Any) -> None:
        if self._start is None:
            return
        self._paint(self._start[0], self._start[1], event.x, event.y)

    def _on_release(self, event: Any) -> None:
        if self._start is None:
            return
        box = self._to_image_box(self._start[0], self._start[1], event.x, event.y)
        if box[2] - box[0] < MIN_SELECTION_PX or box[3] - box[1] < MIN_SELECTION_PX:
            self._cancel()
            return
        self._box = box
        self._close()

    def _on_cancel(self, _event: Any = None) -> None:
        self._cancel()

    # -- painting ---------------------------------------------------------- #

    def _viewport_size(self) -> tuple[int, int]:
        """The size of the visible canvas (native fullscreen excludes the menu bar)."""
        if self._canvas is None:
            return self._screen
        width, height = self._canvas.winfo_width(), self._canvas.winfo_height()
        return (
            width if width > 1 else self._screen[0],
            height if height > 1 else self._screen[1],
        )

    def _paint(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if self._canvas is None:
            return
        screen_width, screen_height = self._viewport_size()
        left, right = sorted(
            (max(0, min(x1, screen_width)), max(0, min(x2, screen_width)))
        )
        top, bottom = sorted(
            (max(0, min(y1, screen_height)), max(0, min(y2, screen_height)))
        )

        canvas = self._canvas
        canvas.coords(self._veil[0], 0, 0, screen_width, top)
        canvas.coords(self._veil[1], 0, bottom, screen_width, screen_height)
        canvas.coords(self._veil[2], 0, top, left, bottom)
        canvas.coords(self._veil[3], right, top, screen_width, bottom)
        for item in self._veil:
            canvas.itemconfigure(item, state="normal")

        if self._outline is not None:
            canvas.coords(self._outline, left, top, right, bottom)
            canvas.itemconfigure(self._outline, state="normal")

        box = self._to_image_box(left, top, right, bottom)
        self._place_label(f"{box[2] - box[0]} x {box[3] - box[1]}", left, top)

    def _place_label(self, text: str, left: int, top: int) -> None:
        if self._canvas is None or self._label is None or self._label_bg is None:
            return
        canvas = self._canvas
        screen_width, screen_height = self._viewport_size()

        canvas.itemconfigure(self._label, text=text, state="normal")
        canvas.coords(self._label, 0, 0)
        bbox = canvas.bbox(self._label)
        text_width = (bbox[2] - bbox[0]) if bbox else 60
        text_height = (bbox[3] - bbox[1]) if bbox else 16

        x = min(left + 8, max(screen_width - text_width - 12, 0))
        y = min(top + 8, max(screen_height - text_height - 12, 0))
        canvas.coords(self._label, x, y)
        canvas.coords(
            self._label_bg,
            x - 4,
            y - 2,
            x + text_width + 4,
            y + text_height + 2,
        )
        canvas.itemconfigure(self._label_bg, state="normal")
        canvas.tag_raise(self._label_bg)
        canvas.tag_raise(self._label)

    # -- mapping ----------------------------------------------------------- #

    def _to_image_box(self, x1: float, y1: float, x2: float, y2: float) -> Box:
        scale = self._scale
        from .capture import normalize_box

        return normalize_box(
            (x1 * scale, y1 * scale, x2 * scale, y2 * scale), self._image.size
        )

    # -- teardown ---------------------------------------------------------- #

    def _cancel(self) -> None:
        self._box = None
        self._close()

    def _close(self) -> None:
        if self._root is None or self._tk is None:
            return
        try:
            self._root.quit()
        except self._tk.TclError:  # pragma: no cover - window already gone
            pass

    def _destroy(self) -> None:
        if self._root is None or self._tk is None:
            return
        try:
            self._root.destroy()
        except self._tk.TclError:  # pragma: no cover
            pass
        finally:
            self._root = None
            self._canvas = None
            self._photos = ()
