#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

uv sync --extra mac --extra hotkey --extra bundle --extra dev

# py2app 0.28 rejects the install_requires that setuptools copies from
# pyproject.toml, so build from a scratch directory with no pyproject.
BUILD_DIR="$ROOT/.build_app"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
cp packaging/py2app_setup.py "$BUILD_DIR/setup.py"

(
  cd "$BUILD_DIR"
  STT_ROOT="$ROOT" uv run --no-sync python setup.py py2app
)

mkdir -p "$ROOT/dist"
rm -rf "$ROOT/dist/stt.app"
mv "$BUILD_DIR/dist/stt.app" "$ROOT/dist/stt.app"
rm -rf "$BUILD_DIR"

echo "Built dist/stt.app"
echo "Grant it Screen Recording and Accessibility in System Settings, then launch it."
