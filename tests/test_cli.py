"""CLI wiring: exit codes, the no-GUI ``layout`` command, and the grab flow."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from screen_ocr.main import EXIT_CANCELLED, EXIT_ERROR, EXIT_OK, main
from screen_ocr.models import Token


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0


def test_layout_reads_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tokens = [
        {"text": "def", "left": 0, "top": 0, "width": 27, "height": 18},
        {"text": "f():", "left": 36, "top": 0, "width": 36, "height": 18},
        {"text": "pass", "left": 36, "top": 18, "width": 36, "height": 18},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(tokens)))

    assert main(["layout"]) == EXIT_OK
    assert capsys.readouterr().out == "def f():\n    pass"


def test_layout_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "tokens": [
                    {"text": "a", "left": 0, "top": 0, "width": 9, "height": 18},
                    {"text": "b", "left": 90, "top": 0, "width": 9, "height": 18},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert main(["layout", str(path)]) == EXIT_OK


def test_layout_rejects_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    assert main(["layout", str(path)]) == EXIT_ERROR
    assert "cannot read tokens" in capsys.readouterr().err


def test_layout_reports_empty_selection_as_empty_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("[]"))

    assert main(["layout"]) == EXIT_OK
    assert capsys.readouterr().out == ""


def test_layout_can_emit_tab_indentation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tokens = [
        {"text": "root", "left": 0, "top": 0, "width": 28, "height": 18},
        {"text": "next", "left": 28, "top": 18, "width": 28, "height": 18},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(tokens)))

    assert main(["layout", "--indent", "tabs"]) == EXIT_OK
    assert capsys.readouterr().out == "root\n\tnext"


# --------------------------------------------------------------------------- #
# grab flow, with the screen and the OCR engine faked out
# --------------------------------------------------------------------------- #


class FakeBackend:
    name = "fake"

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens

    def read_tokens(self, _image: Image.Image) -> list[Token]:
        return self._tokens


class FakeOverlay:
    box: tuple[int, int, int, int] | None = (0, 0, 100, 50)

    def __init__(self, _image: Image.Image, **_kwargs: object) -> None:
        pass

    def run(self) -> tuple[int, int, int, int] | None:
        return type(self).box


@pytest.fixture
def grab_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    copied: dict[str, str] = {}
    tokens = [Token("def", 0, 0, 27, 18), Token("f():", 36, 0, 36, 18)]

    monkeypatch.setattr(
        "screen_ocr.main.grab_desktop", lambda: Image.new("RGB", (200, 100), "white")
    )
    monkeypatch.setattr("screen_ocr.main.is_blank", lambda _image: False)
    monkeypatch.setattr(
        "screen_ocr.main.create_backend", lambda *_a, **_k: FakeBackend(tokens)
    )
    monkeypatch.setattr("screen_ocr.overlay.SelectionOverlay", FakeOverlay)

    def fake_copy(text: str) -> bool:
        copied["text"] = text
        return True

    monkeypatch.setattr("screen_ocr.main.copy_to_clipboard", fake_copy)
    return copied


def test_grab_copies_reconstructed_text(
    grab_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["grab"]) == EXIT_OK

    assert grab_env["text"] == "def f():"
    # Silence on success: nothing on stdout or stderr.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_bare_command_uses_grab_defaults(grab_env: dict[str, str]) -> None:
    assert main([]) == EXIT_OK
    assert grab_env["text"] == "def f():"


def test_grab_cancel_leaves_clipboard_untouched(
    grab_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeOverlay, "box", None)

    assert main(["grab"]) == EXIT_CANCELLED
    assert grab_env == {}


def test_grab_blank_screen_reports_permission_hint(
    grab_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("screen_ocr.main.is_blank", lambda _image: True)

    assert main(["grab"]) == EXIT_ERROR
    assert "Screen Recording" in capsys.readouterr().err
    assert grab_env == {}


def test_grab_without_text_leaves_clipboard_untouched(
    grab_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "screen_ocr.main.create_backend", lambda *_a, **_k: FakeBackend([])
    )

    assert main(["grab"]) == EXIT_ERROR
    assert "clipboard left untouched" in capsys.readouterr().err
    assert grab_env == {}


def test_grab_debug_prints_but_does_not_copy(
    grab_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["grab", "--debug"]) == EXIT_OK

    captured = capsys.readouterr()
    assert "def f():" in captured.out
    assert grab_env == {}
