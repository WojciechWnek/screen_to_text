"""Command line entry point.

``main.py`` stays thin: parse arguments, wire modules together, choose an exit
code. All real logic lives in the module that owns it.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .capture import (
    PERMISSION_HINT,
    CaptureError,
    crop,
    grab_desktop,
    is_blank,
)
from .clipboard import ClipboardError
from .clipboard import copy as copy_to_clipboard
from .layout import DEFAULT_MAX_GAP_SPACES, reconstruct
from .models import Token
from .ocr import OcrError, OcrUnavailable, create_backend, platform_engine

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
# grab
# --------------------------------------------------------------------------- #


def _cmd_grab(args: argparse.Namespace) -> int:
    from .overlay import OverlayError, SelectionOverlay

    try:
        backend = create_backend(args.engine, args.lang)
    except OcrUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        image = grab_desktop()
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if is_blank(image):
        print(PERMISSION_HINT, file=sys.stderr)
        return EXIT_ERROR

    try:
        box = SelectionOverlay(image).run()
    except OverlayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if box is None:
        return EXIT_CANCELLED

    region = crop(image, box)
    try:
        tokens = backend.read_tokens(region)
    except OcrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    text = reconstruct(tokens, min_conf=args.min_conf)

    if args.debug:
        _print_tokens(tokens)
        if text:
            print(text)
        return EXIT_OK

    if not text.strip():
        print("no text found in the selection - clipboard left untouched", file=sys.stderr)
        return EXIT_ERROR

    try:
        copied = copy_to_clipboard(text)
    except ClipboardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not copied:
        print("nothing to copy - clipboard left untouched", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


# --------------------------------------------------------------------------- #
# demo (no screen access, no permissions)
# --------------------------------------------------------------------------- #


def _cmd_demo(args: argparse.Namespace) -> int:
    from .demo import SAMPLE_CODE, render_code

    try:
        backend = create_backend(args.engine, args.lang)
    except OcrUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    image = render_code()
    try:
        tokens = backend.read_tokens(image)
    except OcrError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    text = reconstruct(tokens, min_conf=args.min_conf)

    if args.show_tokens:
        _print_tokens(tokens)

    print(text)

    expected = SAMPLE_CODE.rstrip("\n")
    if text == expected:
        print(f"\n[exact match, {len(tokens)} tokens, {backend.name}]", file=sys.stderr)
        return EXIT_OK

    print(f"\n[differs from the source, {len(tokens)} tokens, {backend.name}]", file=sys.stderr)
    for line in difflib.unified_diff(
        expected.split("\n"), text.split("\n"), "expected", "ocr", lineterm="", n=1
    ):
        print(line, file=sys.stderr)
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
    _report("permissions", _permission_report())
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
    engine = platform_engine()
    module = {"vision": "Vision", "windows": "winsdk", "tesseract": "pytesseract"}[engine]
    if _module_status(module) != "ok":
        extra = {"vision": "mac", "windows": "win", "tesseract": "tesseract"}[engine]
        return f"{engine} (missing - uv sync --extra {extra})"
    if engine == "tesseract":
        return "tesseract (fallback)"
    return f"{engine} (native)"


def _permission_report() -> str:
    from .capture import permission_ok

    try:
        allowed = permission_ok()
    except Exception as exc:  # noqa: BLE001 - doctor must never crash
        return f"unknown ({exc})"
    if allowed:
        return "screen recording ok"
    return (
        "BLOCKED - System Settings > Privacy & Security > Screen Recording "
        "(then reopen the terminal)"
    )


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

    demo = subparsers.add_parser(
        "demo",
        help="render a sample snippet and OCR it - no screen access needed",
    )
    demo.add_argument(
        "--engine",
        choices=("auto", "vision", "windows", "tesseract"),
        default="auto",
    )
    demo.add_argument("--lang", default=None)
    demo.add_argument("--min-conf", type=float, default=0.0)
    demo.add_argument("--show-tokens", action="store_true")
    demo.set_defaults(handler=_cmd_demo)

    doctor = subparsers.add_parser("doctor", help="report environment, engines and displays")
    doctor.set_defaults(handler=_cmd_doctor)

    return parser


if __name__ == "__main__":
    raise SystemExit(main())
