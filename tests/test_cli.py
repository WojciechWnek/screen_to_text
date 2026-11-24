"""CLI wiring tests: exit codes and the no-GUI ``layout`` subcommand."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from screen_ocr.main import EXIT_ERROR, EXIT_OK, main


def test_version_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0


def test_grab_is_not_wired_up_yet(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["grab"]) == EXIT_ERROR
    assert "Phase 2" in capsys.readouterr().err


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
