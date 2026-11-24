# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows ``stt.exe`` hotkey daemon.

Build from the repository root:

    uv run pyinstaller packaging/stt.spec --noconfirm
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

hiddenimports = collect_submodules("screen_ocr")
hiddenimports += [
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._win32",
]

a = Analysis(
    [str(SRC / "screen_ocr" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    excludes=["Vision", "Quartz"],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="stt",
    console=False,  # no terminal: the hotkey daemon is the UI
    onefile=True,
    icon=None,
)
