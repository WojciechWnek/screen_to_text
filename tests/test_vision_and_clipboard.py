"""Vision box conversion and clipboard purity."""

from __future__ import annotations

import pytest

from screen_ocr.clipboard import ClipboardError, copy
from screen_ocr.ocr.vision import normalized_box_to_pixels


def test_full_image_box() -> None:
    assert normalized_box_to_pixels((0.0, 0.0, 1.0, 1.0), 100, 50) == (0.0, 0.0, 100.0, 50.0)


def test_top_of_image_is_flipped() -> None:
    # Vision: y is measured from the bottom, so the top line has y + height = 1.
    assert normalized_box_to_pixels((0.1, 0.9, 0.2, 0.1), 1000, 100) == (
        100.0,
        0.0,
        200.0,
        10.0,
    )


def test_bottom_of_image_is_flipped() -> None:
    assert normalized_box_to_pixels((0.0, 0.0, 0.5, 0.1), 1000, 100) == (
        0.0,
        90.0,
        500.0,
        10.0,
    )


def test_blank_text_never_touches_the_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_text: str) -> None:
        raise AssertionError("clipboard must not be written for blank text")

    monkeypatch.setattr("screen_ocr.clipboard.pyperclip.copy", explode)

    assert copy("") is False
    assert copy("   \n\t ") is False


def test_copy_passes_text_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "screen_ocr.clipboard.pyperclip.copy",
        lambda text: captured.setdefault("text", text),
    )

    assert copy("def f():\n    return 1") is True
    assert captured["text"] == "def f():\n    return 1"


def test_clipboard_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    def explode(_text: str) -> None:
        raise pyperclip.PyperclipException("boom")

    monkeypatch.setattr("screen_ocr.clipboard.pyperclip.copy", explode)

    with pytest.raises(ClipboardError):
        copy("text")
