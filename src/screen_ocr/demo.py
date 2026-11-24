"""Synthetic sample used by ``stt demo`` and the render round-trip test.

Rendering known text to an image and OCR-ing it back is the only end-to-end
check that needs neither screen capture nor Screen Recording permission.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SAMPLE_CODE = (
    "def process(items):\n"
    "    for item in items:\n"
    "        if item.active:\n"
    "            results.append(item)\n"
    "    return results\n"
)

_MONOSPACE_FONTS = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/Library/Fonts/Andale Mono.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

LINE_SPACING = 1.5


def monospace_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _MONOSPACE_FONTS:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_code(
    text: str = SAMPLE_CODE,
    *,
    font_size: int = 14,
    supersample: int = 2,
    padding: int = 16,
    background: str = "#1e2127",
    foreground: str = "#d7dae0",
) -> Image.Image:
    """Draw code on a dark background, mimicking a terminal screenshot.

    ``supersample`` renders large and downscales, which is what a Retina
    display does before mss hands us nominal-resolution pixels.
    """
    scale = max(supersample, 1)
    font = monospace_font(font_size * scale)
    lines = text.rstrip("\n").split("\n")

    line_height = int(round(font_size * scale * LINE_SPACING))
    widest = max(font.getbbox(line)[2] for line in lines)
    margin = padding * scale
    width = widest + margin * 2
    height = line_height * len(lines) + margin * 2

    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text(
            (margin, margin + index * line_height),
            line,
            font=font,
            fill=foreground,
        )

    if scale > 1:
        image = image.resize(
            (width // scale, height // scale), Image.Resampling.LANCZOS
        )
    return image
