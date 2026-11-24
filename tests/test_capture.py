"""Capture invariants: 1:1 cropping, box normalisation, no disk writes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from screen_ocr import capture
from screen_ocr.capture import CaptureError, Monitor, crop, is_blank, normalize_box


def _content_image() -> Image.Image:
    image = Image.new("RGB", (200, 100), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 60, 60), fill="black")
    return image


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


def test_mac_grab_uses_screencapturekit(tmp_path: Path, monkeypatch) -> None:
    monitor = Monitor(0, 0, 200, 100)
    calls: list[str] = []
    monkeypatch.setattr(capture.sys, "platform", "darwin")
    monkeypatch.setattr(capture, "primary_monitor", lambda: monitor)

    def fake_sck(target: Monitor) -> Image.Image:
        calls.append("sck")
        assert target == monitor
        return _content_image()

    def fail_mss(_monitor: Monitor) -> Image.Image:
        raise AssertionError("mss used")

    monkeypatch.setattr(capture, "_grab_with_screencapturekit", fake_sck)
    monkeypatch.setattr(capture, "_grab_with_mss", fail_mss)
    monkeypatch.chdir(tmp_path)

    assert capture.grab_desktop().size == (200, 100)
    assert calls == ["sck"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="ScreenCaptureKit is macOS-only")
def test_real_mac_capture_writes_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    try:
        image = capture.grab_desktop()
    except CaptureError as exc:  # noqa: PT011 - permissions vary by runner
        pytest.skip(str(exc))
    if is_blank(image):
        pytest.skip("screen recording is blocked or the display is locked")

    assert list(tmp_path.iterdir()) == []


def test_mac_grab_falls_back_to_mss(tmp_path: Path, monkeypatch) -> None:
    monitor = Monitor(0, 0, 200, 100)
    calls: list[str] = []
    monkeypatch.setattr(capture.sys, "platform", "darwin")
    monkeypatch.setattr(capture, "primary_monitor", lambda: monitor)

    def fail_sck(_monitor: Monitor) -> Image.Image:
        calls.append("sck")
        raise CaptureError("ScreenCaptureKit unavailable")

    def fake_mss(_monitor: Monitor) -> Image.Image:
        calls.append("mss")
        return _content_image()

    monkeypatch.setattr(capture, "_grab_with_screencapturekit", fail_sck)
    monkeypatch.setattr(capture, "_grab_with_mss", fake_mss)
    monkeypatch.chdir(tmp_path)

    assert capture.grab_desktop().size == (200, 100)
    assert calls == ["sck", "mss"]
    assert list(tmp_path.iterdir()) == []
