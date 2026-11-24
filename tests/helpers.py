"""Test helpers: build synthetic OCR token geometry without any OCR engine.

Keeping these separate means ``test_layout.py`` is a pure-function test: no
images, no permissions, no flakiness.
"""

from __future__ import annotations

from screen_ocr.models import Token

CHAR_W = 10.0
LINE_H = 20.0


def token(
    text: str,
    *,
    col: float = 0.0,
    line: int = 0,
    top: float | None = None,
    char_w: float = CHAR_W,
    line_h: float = LINE_H,
    conf: float = 1.0,
    width: float | None = None,
) -> Token:
    """One token at a character column and a line index."""
    return Token(
        text=text,
        left=col * char_w,
        top=line * line_h if top is None else top,
        width=len(text) * char_w if width is None else width,
        height=line_h,
        conf=conf,
    )


def row(
    text: str,
    *,
    line: int = 0,
    indent: int = 0,
    gap: int = 1,
    char_w: float = CHAR_W,
    line_h: float = LINE_H,
    conf: float = 1.0,
) -> list[Token]:
    """Tokens for one line of space-separated words, starting at ``indent``."""
    tokens: list[Token] = []
    col = indent
    for word in text.split(" "):
        tokens.append(
            token(
                word,
                col=col,
                line=line,
                char_w=char_w,
                line_h=line_h,
                conf=conf,
            )
        )
        col += len(word) + gap
    return tokens
