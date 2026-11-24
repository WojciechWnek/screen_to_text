"""Layout reconstruction: OCR token geometry -> text with indentation.

This module is intentionally pure: no I/O, no GUI, no OCR imports. Everything
that makes the output look like the original screen (leading spaces, column
gaps, blank lines) is decided here, which is why it is the most heavily tested
part of the project.

The pipeline is:

1. drop empty / low-confidence tokens
2. cluster tokens into visual lines by vertical overlap
3. estimate the character cell width and line height
4. leading spaces per line from its x-offset relative to the leftmost line
5. spaces between tokens from their horizontal gap
6. blank lines from vertical gaps
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from .models import Token

#: Tokens shorter than this are excluded from the character-width estimate:
#: a 1-2 character token ("if", "x") has too much bearing/padding noise.
MIN_CHARS_FOR_WIDTH = 3

#: Hard cap so one stray detection far to the right cannot emit a wall of spaces.
DEFAULT_MAX_GAP_SPACES = 40

#: Hard cap on consecutive inserted blank lines.
DEFAULT_MAX_BLANK_LINES = 5

#: A vertical gap larger than this many line heights means a blank line.
BLANK_LINE_FACTOR = 1.5

#: Two tokens belong to the same line if their vertical spans overlap by more
#: than this fraction of the smaller height. Tolerates italics, subscripts and
#: slightly uneven baselines.
OVERLAP_RATIO = 0.5

#: Typical monospace advance width as a fraction of the line height, used only
#: when the tokens are too short to measure it.
FALLBACK_CHAR_WIDTH_RATIO = 0.6


def reconstruct(
    tokens: Sequence[Token],
    *,
    min_conf: float = 0.0,
    max_gap_spaces: int = DEFAULT_MAX_GAP_SPACES,
    max_blank_lines: int = DEFAULT_MAX_BLANK_LINES,
) -> str:
    """Rebuild indented, line-broken text from OCR tokens.

    Returns ``""`` when there is nothing usable, so the caller can leave the
    user's clipboard untouched.
    """
    kept = [token for token in tokens if token.text.strip() and token.conf >= min_conf]
    if not kept:
        return ""

    lines = _cluster_lines(kept)
    line_height = _estimate_line_height(kept)
    char_width = _estimate_char_width(kept, line_height)

    # With a single line there is no column 0 to compare against, so a copied
    # fragment never gains phantom leading spaces.
    single_line = len(lines) == 1
    min_left = min(min(token.left for token in line) for line in lines)

    rendered: list[str] = []
    for line in lines:
        indent = 0
        if not single_line:
            indent = _clamp_spaces(
                round((min(token.left for token in line) - min_left) / char_width),
                max_gap_spaces,
            )
        rendered.append(
            _render_line(
                line,
                indent=indent,
                char_width=char_width,
                max_gap_spaces=max_gap_spaces,
            )
        )

    return _join_with_blank_lines(
        lines, rendered, line_height=line_height, max_blank_lines=max_blank_lines
    )


def _cluster_lines(tokens: Sequence[Token]) -> list[list[Token]]:
    """Group tokens into visual lines, top to bottom."""
    ordered = sorted(tokens, key=lambda token: (token.top, token.left))
    lines: list[list[Token]] = []
    for token in ordered:
        if lines and _shares_line(lines[-1], token):
            lines[-1].append(token)
        else:
            lines.append([token])
    return lines


def _shares_line(line: Sequence[Token], token: Token) -> bool:
    line_top = min(existing.top for existing in line)
    line_bottom = max(existing.bottom for existing in line)
    overlap = min(line_bottom, token.bottom) - max(line_top, token.top)
    smaller_height = min(token.height, min(existing.height for existing in line))
    if smaller_height <= 0:
        return False
    return overlap > OVERLAP_RATIO * smaller_height


def _estimate_line_height(tokens: Sequence[Token]) -> float:
    heights = [token.height for token in tokens if token.height > 0]
    if heights:
        return statistics.median(heights)
    return 1.0


def _estimate_char_width(tokens: Sequence[Token], line_height: float) -> float:
    measured = [
        token.width / len(token.text)
        for token in tokens
        if len(token.text) >= MIN_CHARS_FOR_WIDTH and token.width > 0
    ]
    if not measured:
        measured = [
            token.width / len(token.text)
            for token in tokens
            if token.text and token.width > 0
        ]
    if measured:
        width = statistics.median(measured)
        if width > 0:
            return width
    return max(line_height * FALLBACK_CHAR_WIDTH_RATIO, 1.0)


def _render_line(
    tokens: Sequence[Token],
    *,
    indent: int,
    char_width: float,
    max_gap_spaces: int,
) -> str:
    pieces = [" " * indent]
    previous: Token | None = None
    for token in sorted(tokens, key=lambda item: item.left):
        text = token.text.strip()
        if not text:
            continue
        if previous is not None:
            gap = token.left - previous.right
            pieces.append(" " * _gap_spaces(gap, char_width, max_gap_spaces))
        pieces.append(text)
        previous = token
    return "".join(pieces).rstrip()


def _gap_spaces(gap: float, char_width: float, max_gap_spaces: int) -> int:
    if char_width <= 0:
        return 1
    # Never zero: adjacent tokens are separate words even when their bounding
    # boxes touch or overlap slightly.
    return max(1, min(int(round(gap / char_width)), max_gap_spaces))


def _clamp_spaces(count: float, max_gap_spaces: int) -> int:
    return max(0, min(int(count), max_gap_spaces))


def _join_with_blank_lines(
    lines: Sequence[Sequence[Token]],
    rendered: Sequence[str],
    *,
    line_height: float,
    max_blank_lines: int,
) -> str:
    if not rendered:
        return ""

    output: list[str] = [rendered[0]]
    for previous_line, line, text in zip(lines, lines[1:], rendered[1:]):
        gap = min(token.top for token in line) - max(token.bottom for token in previous_line)
        if line_height > 0 and gap > BLANK_LINE_FACTOR * line_height:
            count = min(max(round(gap / line_height) - 1, 0), max_blank_lines)
            output.extend([""] * count)
        output.append(text)

    # Trailing blank lines carry no information and only annoy on paste.
    while output and not output[-1]:
        output.pop()
    return "\n".join(output)
