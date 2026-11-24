"""Capture invariants: 1:1 cropping, box normalisation, no disk writes."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from screen_ocr.capture import crop, is_blank, normalize_box


def test_crop_is_one_to_one() -> None:
    image = Image.new("RGB", (200, 100))

    result = crop(image, (10, 20, 110, 70))

    assert result.size == (100, 50)


def test_crop_accepts_any_corner_order() -> None:
    image = Image.new("RGB", (100, 100))

    assert crop(image, (80, 90, 20, 10)).size == (60, 80)


def test_crop_clamps_to_image_bounds() -> None:
    image = Image.new("RGB", (100, 100))

    assert crop(image, (-10, -10, 50, 50)).size == (50, 50)
    assert crop(image, (90, 90, 500, 500)).size == (10, 10)


def test_normalize_box_rounds_floats() -> None:
    assert normalize_box((1.4, 2.6, 9.5, 0.2), (100, 100)) == (1, 0, 10, 3)


def test_is_blank_true_for_flat_frame() -> None:
    assert is_blank(Image.new("RGB", (200, 100), "#2d2d2d"))


def test_is_blank_false_for_content() -> None:
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 60, 60), fill="black")

    assert not is_blank(image)


def test_crop_writes_no_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    image = Image.new("RGB", (200, 100), "white")

    crop(image, (0, 0, 100, 50))

    assert list(tmp_path.iterdir()) == []
