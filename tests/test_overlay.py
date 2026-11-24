"""Selection overlay tests.

These open a real (briefly visible) Tk window and synthesise mouse events, so
they are skipped when no display is available. They are the guard against the
one thing that would silently ruin every capture: coordinate drift between what
the user dragged and what gets cropped.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from PIL import Image  # noqa: E402

from screen_ocr.overlay import SelectionOverlay  # noqa: E402


def _tk_root() -> object:
    root = tk.Tk()
    root.withdraw()
    return root


def _screen_size() -> tuple[int, int]:
    root = _tk_root()
    try:
        return root.winfo_screenwidth(), root.winfo_screenheight()  # type: ignore[attr-defined]
    finally:
        root.destroy()  # type: ignore[attr-defined]


def _display_available() -> bool:
    try:
        _screen_size()
    except Exception:  # noqa: BLE001 - headless CI
        return False
    return True


pytestmark = pytest.mark.skipif(not _display_available(), reason="no display available")


class ScriptedOverlay(SelectionOverlay):
    """Drives a selection programmatically instead of waiting for a human."""

    def __init__(
        self,
        image: Image.Image,
        drag: tuple[int, int, int, int] | None,
        *,
        escape: bool = False,
        delay_ms: int = 120,
    ) -> None:
        super().__init__(image)
        self._drag = drag
        self._escape = escape
        self._delay_ms = delay_ms

    def _build(self, root: tk.Tk) -> None:
        super()._build(root)
        root.after(self._delay_ms, self._play)

    def _play(self) -> None:
        if self._root is None or self._canvas is None:
            return
        if self._escape or self._drag is None:
            self._root.event_generate("<Escape>")
            return
        x1, y1, x2, y2 = self._drag
        canvas = self._canvas
        canvas.event_generate("<ButtonPress-1>", x=x1, y=y1)
        canvas.event_generate("<B1-Motion>", x=x2, y=y2)
        canvas.event_generate("<ButtonRelease-1>", x=x2, y=y2)


def test_selection_maps_one_to_one_when_image_matches_screen() -> None:
    width, height = _screen_size()
    image = Image.new("RGB", (width, height), "white")

    box = ScriptedOverlay(image, (100, 100, 400, 300)).run()

    assert box == (100, 100, 400, 300)


def test_selection_scales_to_image_pixels() -> None:
    width, height = _screen_size()
    # Twice the screen: what a Retina capture looks like if a backend ever
    # returns backing-store pixels.
    image = Image.new("RGB", (width * 2, height * 2), "white")

    box = ScriptedOverlay(image, (100, 100, 400, 300)).run()

    assert box == (200, 200, 800, 600)


def test_selection_is_clamped_to_image() -> None:
    width, height = _screen_size()
    image = Image.new("RGB", (width, height), "white")

    box = ScriptedOverlay(image, (10, 10, width + 500, height + 500)).run()

    assert box is not None
    assert box[2] == width
    assert box[3] == height


def test_tiny_drag_cancels() -> None:
    width, height = _screen_size()
    image = Image.new("RGB", (width, height), "white")

    assert ScriptedOverlay(image, (50, 50, 51, 51)).run() is None


def test_escape_cancels() -> None:
    width, height = _screen_size()
    image = Image.new("RGB", (width, height), "white")

    assert ScriptedOverlay(image, None, escape=True).run() is None
