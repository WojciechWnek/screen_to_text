"""Build the macOS ``stt.app`` with py2app.

Run from the repository root:

    uv run python packaging/py2app_setup.py py2app
"""

from __future__ import annotations

import os
import sysconfig
import zlib
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("STT_ROOT", str(HERE.parent)))
SRC = ROOT / "src"

# CPython 3.13 builds zlib into the interpreter, so py2app 0.28's attempt to
# copy zlib.__file__ fails. Point it at any real stdlib file; the built-in
# module still wins at runtime.
if not hasattr(zlib, "__file__"):
    zlib.__file__ = sysconfig.__file__

setup(
    name="stt",
    app=[str(SRC / "screen_ocr" / "launcher.py")],
    package_dir={"": str(SRC)},
    setup_requires=["py2app"],
    # Setuptools would otherwise copy [project].dependencies here, which
    # py2app 0.28 explicitly refuses. The venv already has them installed.
    install_requires=[],
    options={
        "py2app": {
            "argv_emulation": False,
            "packages": ["screen_ocr", "screen_ocr.ocr", "pynput"],
            "includes": ["Vision", "Quartz", "pynput.*"],
            "excludes": ["pytest", "pytest_cov", "tkinter.test"],
            "plist": {
                "CFBundleName": "stt",
                "CFBundleIdentifier": "com.local.screen-text-grab",
                "LSUIElement": True,
                "NSHighResolutionCapable": True,
            },
        }
    },
)
