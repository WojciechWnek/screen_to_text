"""Full pipeline with the real OCR engine and the real overlay.

Only two things are faked: the desktop image (a rendered snippet instead of your
screen) and the clipboard (so tests never clobber it). Everything between them -
overlay drag -> pixel box -> crop -> Vision -> layout -> copy - is the shipping
code path.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from PIL import Image  # noqa: E402

from screen_ocr.demo import SAMPLE_CODE, render_code  # noqa: E402
from screen_ocr.main import EXIT_OK, main  # noqa: E402
from screen_ocr.ocr import OcrUnavailable, create_backend  # noqa: E402
from screen_ocr.overlay import SelectionOverlay  # noqa: E402


def _screen_size() -> tuple[int, int]:
    root = tk.Tk()
    try:
        root.withdraw()
        return root.winfo_screenwidth(), root.winfo_screenheight()
    finally:
        root.destroy()


def _usable() -> bool:
    try:
        _screen_size()
        create_backend().read_tokens(render_code("probe", font_size=20))
    except (OcrUnavailable, Exception):  # noqa: BLE001
        return False
    return True


pytestmark = pytest.mark.skipif(not _usable(), reason="needs a display and an OCR backend")

OFFSET = (120, 120)
MARGIN = 6


class _ScriptedOverlay(SelectionOverlay):
    """Drags a fixed rectangle instead of waiting for a human."""

    def __init__(self, image: Image.Image, drag: tuple[int, int, int, int]) -> None:
        super().__init__(image)
        self._drag = drag

    def _build(self, root: tk.Tk) -> None:
        super()._build(root)
        root.after(150, self._play)

    def _play(self) -> None:
        if self._root is None or self._canvas is None:
            return
        x1, y1, x2, y2 = self._drag
        canvas = self._canvas
        canvas.event_generate("<ButtonPress-1>", x=x1, y=y1)
        canvas.event_generate("<B1-Motion>", x=x2, y=y2)
        canvas.event_generate("<ButtonRelease-1>", x=x2, y=y2)


def test_full_pipeline_copies_the_selected_text(monkeypatch: pytest.MonkeyPatch) -> None:
    width, height = _screen_size()
    snippet = render_code()
    desktop = Image.new("RGB", (width, height), "#101418")
    desktop.paste(snippet, OFFSET)

    drag = (
        OFFSET[0] - MARGIN,
        OFFSET[1] - MARGIN,
        min(OFFSET[0] + snippet.width + MARGIN, width),
        min(OFFSET[1] + snippet.height + MARGIN, height),
    )

    copied: dict[str, str] = {}

    def fake_copy(text: str) -> bool:
        copied["text"] = text
        return True

    monkeypatch.setattr("screen_ocr.main.grab_desktop", lambda: desktop)
    monkeypatch.setattr("screen_ocr.main.is_blank", lambda _image: False)
    monkeypatch.setattr(
        "screen_ocr.overlay.SelectionOverlay",
        lambda image, **_kwargs: _ScriptedOverlay(image, drag),
    )
    monkeypatch.setattr("screen_ocr.main.copy_to_clipboard", fake_copy)

    assert main(["grab"]) == EXIT_OK
    assert copied["text"] == SAMPLE_CODE.rstrip("\n")
