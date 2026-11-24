"""Clipboard injection: text and nothing else."""

from __future__ import annotations

import pyperclip


class ClipboardError(RuntimeError):
    """Raised when the OS clipboard cannot be written."""


def copy(text: str) -> bool:
    """Put ``text`` on the clipboard.

    Returns ``False`` without touching the clipboard when the text is empty or
    whitespace-only, so a failed OCR never destroys what the user had copied.
    """
    if not text.strip():
        return False
    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as exc:
        raise ClipboardError(f"cannot write to the clipboard: {exc}") from exc
    return True
