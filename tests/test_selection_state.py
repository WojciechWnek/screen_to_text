"""Pure selection-geometry tests.

These run without opening a window. They guard the promise that a drag in
viewport points becomes a crop box in captured-image pixels, with no Retina or
DPI maths hiding anywhere else in the codebase.
"""

from __future__ import annotations

from screen_ocr.capture import Box
from screen_ocr.selection_state import SelectionState


def _drag(
    state: SelectionState, x1: float, y1: float, x2: float, y2: float
) -> Box | None:
    state.down(x1, y1)
    state.drag(x2, y2)
    return state.up(x2, y2)


def test_one_to_one_mapping() -> None:
    state = SelectionState((400, 300), (400, 300))

    assert _drag(state, 10, 20, 110, 90) == (10, 20, 110, 90)


def test_backing_store_image_maps_to_points() -> None:
    state = SelectionState((800, 600), (400, 300))

    assert _drag(state, 10, 20, 110, 90) == (20, 40, 220, 180)


def test_drag_is_clamped_to_viewport() -> None:
    state = SelectionState((400, 300), (400, 300))

    assert _drag(state, 10, 10, 900, 900) == (10, 10, 400, 300)
    assert _drag(state, 900, 900, 10, 10) == (10, 10, 400, 300)


def test_tiny_drag_cancels() -> None:
    state = SelectionState((400, 300), (400, 300))

    assert _drag(state, 50, 50, 52, 51) is None
    assert state.cancelled is True
    assert state.box is None


def test_cancel_clears_active_drag() -> None:
    state = SelectionState((400, 300), (400, 300))
    state.down(10, 10)
    state.drag(100, 100)

    assert state.active is True
    assert state.image_box == (10, 10, 100, 100)

    state.cancel()

    assert state.active is False
    assert state.image_box is None
    assert state.selection_points() is None
