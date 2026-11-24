"""Command line entry point.

``main.py`` stays thin: parse arguments, wire modules together, choose an exit
code. All real logic lives in the module that owns it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .layout import DEFAULT_MAX_GAP_SPACES, reconstruct
from .models import Token

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CANCELLED = 2

_STDIN = "-"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return EXIT_CANCELLED


# --------------------------------------------------------------------------- #
# grab (wired up in Phase 2)
# --------------------------------------------------------------------------- #


def _cmd_grab(_args: argparse.Namespace) -> int:
    print(
        "capture is not wired up yet (build plan Phase 2).\n"
        "For now you can exercise the layout engine with: stt layout --help",
        file=sys.stderr,
    )
    return EXIT_ERROR


# --------------------------------------------------------------------------- #
# layout (Phase 1: lets you test the algorithm without a GUI)
# --------------------------------------------------------------------------- #


def _cmd_layout(args: argparse.Namespace) -> int:
    try:
        tokens = _load_tokens(args.path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: cannot read tokens: {exc}", file=sys.stderr)
        return EXIT_ERROR

    text = reconstruct(
        tokens,
        min_conf=args.min_conf,
        max_gap_spaces=args.max_gap_spaces,
    )

    if args.show_tokens:
        _print_tokens(tokens)

    sys.stdout.write(text)
    # A trailing newline is for the terminal's sake only; the clipboard never
    # gets one.
    if text and sys.stdout.isatty():
        sys.stdout.write("\n")
    return EXIT_OK


def _load_tokens(path: str) -> list[Token]:
    raw = sys.stdin.read() if path == _STDIN else Path(path).read_text(encoding="utf-8")
    data: Any = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("tokens", [])
    if not isinstance(data, list):
        raise ValueError("expected a JSON list of tokens, or {\"tokens\": [...]}")
    return [Token.from_mapping(item) for item in data]


def _print_tokens(tokens: Sequence[Token]) -> None:
    print(
        f"{'text':<28} {'left':>7} {'top':>7} {'width':>7} {'height':>7} {'conf':>5}",
        file=sys.stderr,
    )
    for token in tokens:
        print(
            f"{token.text:<28.28} {token.left:>7.1f} {token.top:>7.1f} "
            f"{token.width:>7.1f} {token.height:>7.1f} {token.conf:>5.2f}",
            file=sys.stderr,
        )


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _cmd_doctor(_args: argparse.Namespace) -> int:
    print(f"screen-text-grab {__version__}")
    _report("platform", f"{platform.system()} {platform.release()} ({platform.machine()})")
    _report("python", f"{platform.python_version()}  {sys.executable}")
    _report("backend", _detect_backend())

    print()
    print("dependencies")
    _report("  mss", _module_status("mss"))
    _report("  pillow", _module_status("PIL"))
    _report("  pyperclip", _module_status("pyperclip"))
    _report("  tkinter", _module_status("tkinter"))

    print()
    print("ocr engines")
    _report("  vision", _module_status("Vision"), suffix="(macOS)")
    _report("  windows ocr", _module_status("winsdk"), suffix="(Windows)")
    _report("  tesseract", _tesseract_status(), suffix="(optional)")

    print()
    print("displays")
    _report("  monitors", _monitor_report())
    _report("  tk screen", _tk_report())

    print()
    _report("permissions", "checked at capture time (Phase 2)")
    return EXIT_OK


def _report(label: str, value: str, *, suffix: str = "") -> None:
    tail = f"  {suffix}" if suffix else ""
    print(f"{label:<14} {value}{tail}")


def _module_status(module: str) -> str:
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    return "ok" if found else "missing"


def _tesseract_status() -> str:
    binary = shutil.which("tesseract")
    if binary is None:
        return "missing"
    return f"ok  {binary}"


def _detect_backend() -> str:
    system = platform.system()
    if system == "Darwin":
        if _module_status("Vision") == "ok":
            return "vision (macOS native)"
        return "unavailable - install with: uv sync --extra mac"
    if system == "Windows":
        if _module_status("winsdk") == "ok":
            return "windows ocr (native)"
        return "unavailable - install with: uv sync --extra win"
    if _module_status("pytesseract") == "ok":
        return "tesseract (fallback)"
    return "unavailable - install the [tesseract] extra"


def _monitor_report() -> str:
    try:
        import mss
    except ImportError:
        return "unavailable (install mss)"
    try:
        with mss.mss() as sct:
            monitors = list(sct.monitors)
    except Exception as exc:  # noqa: BLE001 - report, never crash doctor
        return f"unavailable ({exc})"

    displays = monitors[1:]  # index 0 is the virtual desktop union
    if not displays:
        return "none detected"
    described = ", ".join(
        f"{monitor['width']}x{monitor['height']}+{monitor['left']}+{monitor['top']}"
        for monitor in displays
    )
    return f"{len(displays)} display(s): {described}"


def _tk_report() -> str:
    try:
        import tkinter
    except ImportError:
        return "unavailable (install tkinter)"

    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        return f"{root.winfo_screenwidth()}x{root.winfo_screenheight()} points"
    except Exception as exc:  # noqa: BLE001 - report, never crash doctor
        return f"unavailable ({exc})"
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# argument parsing
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stt",
        description="Select a region of the screen and put its text on the clipboard.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.set_defaults(handler=_cmd_grab)

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    grab = subparsers.add_parser("grab", help="select a region and copy its text (default)")
    grab.add_argument(
        "--engine",
        choices=("auto", "vision", "windows", "tesseract"),
        default="auto",
        help="OCR engine to use (default: auto-detect for this platform)",
    )
    grab.add_argument("--lang", default=None, help="OCR language hint, e.g. en-US")
    grab.add_argument(
        "--min-conf",
        type=float,
        default=0.0,
        help="drop OCR tokens below this confidence (0.0-1.0)",
    )
    grab.add_argument("--debug", action="store_true", help="print text and token boxes, do not copy")
    grab.set_defaults(handler=_cmd_grab)

    layout = subparsers.add_parser(
        "layout",
        help="rebuild text from a JSON token list (no screen access)",
    )
    layout.add_argument(
        "path",
        nargs="?",
        default=_STDIN,
        help="JSON file with tokens, or '-' for stdin (default)",
    )
    layout.add_argument("--min-conf", type=float, default=0.0)
    layout.add_argument(
        "--max-gap-spaces",
        type=int,
        default=DEFAULT_MAX_GAP_SPACES,
        help=f"cap on spaces emitted for one gap (default: {DEFAULT_MAX_GAP_SPACES})",
    )
    layout.add_argument(
        "--show-tokens",
        action="store_true",
        help="print the parsed token table to stderr",
    )
    layout.set_defaults(handler=_cmd_layout)

    doctor = subparsers.add_parser("doctor", help="report environment, engines and displays")
    doctor.set_defaults(handler=_cmd_doctor)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
