"""Entry point for bundled apps: start straight into the hotkey daemon."""

from __future__ import annotations

from screen_ocr.main import main


def app_main() -> int:
    """Called by py2app/PyInstaller when the user launches the .app/.exe."""
    return main(["listen", "--quiet"])


if __name__ == "__main__":
    raise SystemExit(app_main())
