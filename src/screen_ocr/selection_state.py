"""Pure pointer-to-pixel state for the selection overlay.

Kept free of AppKit/Tk so the coordinate contract can be tested without opening
a window. Both overlays (native on macOS, Tk on Windows) feed it viewport-space
points with a top-left origin and take back a crop box in captured-image pixels.
"""

from __future__ import annotations

from .capture import Box, normalize_box

#: A drag smaller than this (in image pixels) counts as a cancel.
MIN_SELECTION_PX = 4

#: How much to darken the desktop outside the selection (Windows/Tk veil).
VEIL_ALPHA = 0.45

#: Lighter, single-pass veil used by the native macOS overlay. Applying the
#: veil once (not twice) keeps the preview readable while still marking the
#: area outside the drag.
MAC_VEIL_ALPHA = 0.20


class SelectionState:
    """Tracks one drag and maps it from viewport points to image pixels."""

    def __init__(
        self,
        image_size: tuple[int, int],
        viewport: tuple[int, int],
        *,
        min_px: int = MIN_SELECTION_PX,
    ) -> None:
        self.image_size = image_size
        self.viewport = viewport
        self.min_px = min_px
        self.start: tuple[float, float] | None = None
        self.current: tuple[float, float] | None = None
        self.box: Box | None = None
        self.cancelled = False

    @property
    def active(self) -> bool:
        """True once the pointer has gone down (selection is being drawn)."""
        return self.start is not None

    def down(self, x: float, y: float) -> None:
        self.start = (x, y)
        self.current = (x, y)
        self.box = None

    def drag(self, x: float, y: float) -> None:
        if self.start is not None:
            self.current = (x, y)
            self.box = self._to_box(self._rect(x, y))

    def up(self, x: float, y: float) -> Box | None:
        """Finish the drag; a zero-area or tiny selection cancels."""
        if self.start is None:
            return None
        self.current = (x, y)
        box = self._to_box(self._rect(x, y))
        if box[2] - box[0] < self.min_px or box[3] - box[1] < self.min_px:
            self.cancel()
            return None
        self.box = box
        return box

    def cancel(self) -> None:
        self.cancelled = True
        self.start = None
        self.current = None
        self.box = None

    def _rect(self, x: float, y: float) -> tuple[float, float, float, float]:
        if self.start is None:
            return (0.0, 0.0, 0.0, 0.0)
        width, height = self.viewport
        x1, y1 = self.start
        left, right = sorted((max(0.0, min(x1, width)), max(0.0, min(x, width))))
        top, bottom = sorted((max(0.0, min(y1, height)), max(0.0, min(y, height))))
        return left, top, right, bottom

    def selection_points(self) -> tuple[float, float, float, float] | None:
        """The clamped, ordered drag rectangle in viewport points."""
        if self.start is None or self.current is None:
            return None
        return self._rect(self.current[0], self.current[1])

    @property
    def image_box(self) -> Box | None:
        """The current drag mapped to image pixels, even before mouse-up."""
        rect = self.selection_points()
        return self._to_box(rect) if rect is not None else None

    def veil_rects(self) -> tuple[tuple[float, float, float, float], ...]:
        """Four rectangles covering everything outside the selection."""
        selection = self.selection_points()
        if selection is None:
            return ()
        width, height = self.viewport
        left, top, right, bottom = selection
        return (
            (0.0, 0.0, width, top),
            (0.0, bottom, width, height),
            (0.0, top, left, bottom),
            (right, top, width, bottom),
        )

    def _to_box(self, rect: tuple[float, float, float, float]) -> Box:
        left, top, right, bottom = rect
        view_width, view_height = self.viewport
        scale_x = self.image_size[0] / view_width if view_width else 1.0
        scale_y = self.image_size[1] / view_height if view_height else 1.0
        return normalize_box(
            (left * scale_x, top * scale_y, right * scale_x, bottom * scale_y),
            self.image_size,
        )
