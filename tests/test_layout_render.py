"""End-to-end round trip: render code, OCR it, rebuild it.

This is the only test that exercises a real OCR engine. It needs neither a
screen nor permissions, and it is skipped when no backend is installed.
"""

from __future__ import annotations

import pytest

from screen_ocr.demo import SAMPLE_CODE, render_code
from screen_ocr.layout import reconstruct
from screen_ocr.ocr import OcrUnavailable, create_backend


@pytest.fixture(scope="module")
def backend():
    try:
        chosen = create_backend()
        chosen.read_tokens(render_code("probe", font_size=20))
    except OcrUnavailable as exc:
        pytest.skip(str(exc))
    except Exception as exc:  # noqa: BLE001 - engine present but broken here
        pytest.skip(f"OCR backend unusable: {exc}")
    return chosen


def test_rendered_code_round_trips(backend) -> None:
    tokens = backend.read_tokens(render_code())

    assert tokens, "the OCR engine found no text in a rendered snippet"

    text = reconstruct(tokens)

    assert text == SAMPLE_CODE.rstrip("\n")


def test_round_trip_is_deterministic(backend) -> None:
    image = render_code()

    first = reconstruct(backend.read_tokens(image))
    second = reconstruct(backend.read_tokens(image))

    assert first == second
