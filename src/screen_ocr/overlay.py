"""Frozen-desktop selection overlay.

The desktop is already in memory when this window appears, so the rectangle the
user drags is expressed directly in captured-image pixels. That is what removes
all Retina/DPI coordinate maths from the project.
"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageTk

from .capture import Box, normalize_box

#: A drag smaller than this (in image pixels) counts as a cancel.
MIN_SELECTION_PX = 4

#: How much to darken the parts of the desktop outside the selection.
VEIL_ALPHA = 0.45

OUTLINE_COLOR = "#00d4ff"
LABEL_BACKGROUND = "#101418"
LABEL_FOREGROUND = "#e6f7ff"


class OverlayError(RuntimeError):
    """Raised when the selection window cannot be created."""


class SelectionOverlay:
    def __init__(self, image: Image.Image, *, auto_close_ms: int | None = None) -> None:
        self._image = image
        self._auto_close_ms = auto_close_ms
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._image_item: int | None = None
        self._veil: list[int] = []
        self._outline: int | None = None
        self._label: int | None = None
        self._label_bg: int | None = None
        self._photos: tuple[ImageTk.PhotoImage, ...] = ()
        self._screen = (0, 0)
        self._scale = 1.0
        self._start: tuple[int, int] | None = None
        self._box: Box | None = None

    # -- public API ------------------------------------------------------- #

    def run(self) -> Box | None:
        """Show the overlay and return the selection in image pixels, or None."""
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

    def _build(self, root: tk.Tk) -> None:
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="black")

        width, height = root.winfo_screenwidth(), root.winfo_screenheight()
        self._screen = (width, height)
        root.geometry(f"{width}x{height}+0+0")

        bright = self._image
        if bright.size != (width, height):
            bright = bright.resize((width, height), Image.Resampling.LANCZOS)
        self._scale = self._image.width / width if width else 1.0
        dimmed = Image.blend(
            bright, Image.new("RGB", bright.size, "black"), VEIL_ALPHA
        )
        self._photos = (ImageTk.PhotoImage(bright), ImageTk.PhotoImage(dimmed))

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

    def _on_press(self, event: tk.Event) -> None:
        if self._canvas is None or self._image_item is None:
            return
        self._start = (event.x, event.y)
        # From here on the selected area is shown undimmed: bright image on the
        # canvas, solid black panels covering everything outside the rectangle.
        self._canvas.itemconfigure(self._image_item, image=self._photos[0])
        self._paint(event.x, event.y, event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        if self._start is None:
            return
        self._paint(self._start[0], self._start[1], event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        if self._start is None:
            return
        box = self._to_image_box(self._start[0], self._start[1], event.x, event.y)
        if box[2] - box[0] < MIN_SELECTION_PX or box[3] - box[1] < MIN_SELECTION_PX:
            self._cancel()
            return
        self._box = box
        self._close()

    def _on_cancel(self, _event: tk.Event | None = None) -> None:
        self._cancel()

    # -- painting ---------------------------------------------------------- #

    def _paint(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if self._canvas is None:
            return
        screen_width, screen_height = self._screen
        left, right = sorted((max(0, min(x1, screen_width)), max(0, min(x2, screen_width))))
        top, bottom = sorted((max(0, min(y1, screen_height)), max(0, min(y2, screen_height))))

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
        screen_width, screen_height = self._screen

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
        return normalize_box(
            (x1 * scale, y1 * scale, x2 * scale, y2 * scale), self._image.size
        )

    # -- teardown ---------------------------------------------------------- #

    def _cancel(self) -> None:
        self._box = None
        self._close()

    def _close(self) -> None:
        if self._root is None:
            return
        try:
            self._root.quit()
        except tk.TclError:  # pragma: no cover - window already gone
            pass

    def _destroy(self) -> None:
        if self._root is None:
            return
        try:
            self._root.destroy()
        except tk.TclError:  # pragma: no cover
            pass
        finally:
            self._root = None
            self._canvas = None
            self._photos = ()
