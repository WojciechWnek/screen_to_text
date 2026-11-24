# AGENTS.md

Instructions for AI agents working in this repository. Read this before touching code.
`README.md` is the human-facing spec; this file is the operational rules. If they disagree, fix
whichever is wrong in the same change — do not leave them inconsistent.

## Project Overview

A lightweight cross-platform (macOS + Windows) desktop utility in Python. The user triggers a
capture, drags a rectangle over the screen, and the text inside that rectangle is OCR'd with
**indentation, internal spacing and blank lines preserved**, then copied to the clipboard as plain
text — nothing else. The screenshot is never written to disk.

Status: **Phases 0–2 and 4 are complete** — scaffold, pure layout engine, a working macOS pipeline
(`stt grab`: ScreenCaptureKit → native overlay → Vision → layout → clipboard), and a `pynput` hotkey
daemon with bundles, with 92 tests green. Windows OCR (Phase 3) and the Windows `.exe` build are
**unverified**.

## Locked decisions (do not re-litigate these without asking the user)

These were chosen deliberately, with the trade-offs already weighed. Reversing any of them is a
scope change, not a refactor.

1. **Native OCR backends, no mandatory external binaries.**
   - macOS: Apple **Vision** (`VNRecognizeTextRequest`, `.accurate`) via `pyobjc`.
   - Windows: **`Windows.Media.Ocr`** via `winsdk` (returns true word-level boxes).
   - Optional fallback: **Tesseract** via `pytesseract`, behind the `[tesseract]` extra only.
   - All behind one `OcrBackend` protocol returning `list[Token]`.
2. **Frozen-desktop overlay, NOT a translucent tint.**
   Grab the desktop into memory *first*, then display that image in a full-screen window with a
   dimming veil and a selection rectangle. On mouse release, `image.crop(box)` — the box is already
   in captured-image pixels.
   - macOS: ScreenCaptureKit capture + a native AppKit/Quartz window. Tk must not be imported there.
   - Windows: `mss` capture + the existing Tk/Canvas window. Leave this path unchanged.
   - `selection_state.py` owns the one preview scale factor (`image.width / viewport.width`). It is
     used only to map pointer points to image pixels; never use it to rescale the crop or the text.
   - This is why the captured image is never darkened by the overlay.
   - Do not "simplify" this back into an alpha overlay over the live screen.
3. **`layout.py` is a pure function.** `list[Token] -> str`. No I/O, no Tk, no OCR imports, no
   global state, no config file reads. All of the hard logic lives here and is tested without OCR.
4. **Native AppKit** for the macOS overlay, **Tkinter** for the Windows overlay (no Qt, no webview).
   **ScreenCaptureKit** for macOS capture (macOS 13+), **`mss`** for Windows and as a fallback.
   **`pyperclip`** for the clipboard. **`uv`** for environments, pinned to **Python 3.12 or 3.13** —
   the machine's default 3.15 is too new for reliable `pyobjc` wheels.
5. **CLI-first.** Entry point `stt`. The global hotkey daemon (`pynput`, `stt listen`) and packaging
   arrived in Phase 4, after the core worked. Do not add a tray icon: the bundled app is a
   terminal-less daemon, not a tray UI.

## Hard invariants

Violating any of these is a bug, regardless of what else works.

### 1. No files on disk, ever

- ScreenCaptureKit frames go `CGImage` → `NSBitmapImageRep` → PNG bytes in `BytesIO` → `PIL.Image`.
  `mss` frames go straight to `PIL.Image.frombytes(...)`. No temp files, no `.png`, no `/tmp`, not
  even behind a debug flag.
- The `screencapture -x -t png -R … -` fallback pipes PNG over stdout into PIL. Keep it disk-free.
- Preprocessing (upscale, grayscale, contrast) happens in RAM on the `PIL.Image`.
- `--debug` prints **text** and a token table to stdout. It must never dump an image.
- Add/extend the test that runs a capture in an empty temp cwd and asserts the directory is still
  empty.

### 2. Clipboard purity

- `pyperclip.copy(text)` receives the reconstructed string and nothing else: no trailing newline, no
  header, no "Copied!" marker, no metadata.
- If the result is empty or whitespace-only, **do not touch the clipboard** — leave the user's
  existing contents intact and report to stderr.
- Never put an image on the clipboard.

### 3. The overlay must never trap the user

- `Esc` cancels and returns `None`; the caller then exits without copying.
- Always destroy the Tk root / close the AppKit window in a `finally` block.
- Bind Escape, right-click, window-close and a global mouse-up monitor. Catch `KeyboardInterrupt`.
- Releasing the mouse outside the window, or a zero-area drag, must be treated as a cancel.

### 4. Silence on success

No toast, no dialog, no terminal banner on the happy path. The text appearing on the clipboard *is*
the feedback. `--debug` is the only chatty mode.

## Architecture

```text
src/screen_ocr/
  main.py         # CLI: `stt`, `stt grab`, `stt listen`, `stt demo`, `stt doctor`; wiring only
  models.py       # Token dataclass
  capture.py      # grab_desktop() -> PIL.Image (SCK on macOS), crop(img, box) -> PIL.Image
  overlay.py      # platform dispatch + Windows Tk selection UI, returns box|None
  overlay_macos.py # native AppKit/Quartz selection UI (macOS only)
  selection_state.py # PURE: pointer points -> image-pixel crop box
  layout.py       # PURE: list[Token] -> str
  clipboard.py    # copy(text), silent no-op on blank
  hotkey.py       # pynput listener; queues events, runs grab_once on the main thread
  launcher.py     # bundle entry point: `listen --quiet`
  demo.py         # renders a source sample in RAM for the round-trip check
  ocr/base.py     # OcrBackend protocol
  ocr/vision.py   # macOS
  ocr/windows.py  # Windows
  ocr/tesseract.py
packaging/
  py2app_setup.py # macOS .app
  stt.spec        # Windows .exe
scripts/
  build_macos.sh / build_windows.ps1
tests/
  test_layout.py          # synthetic boxes -> golden strings (primary safety net)
  test_layout_render.py   # render real code with a real monospace font, round-trip
  test_capture.py         # crop invariants + "no files created"
  test_overlay.py         # Windows Tk selection UI, both cancel paths
  test_overlay_macos.py   # native AppKit selection UI and cancel paths
  test_selection_state.py # PURE drag -> pixel mapping
  test_grab_integration.py # overlay -> crop -> OCR -> layout -> clipboard
  test_hotkey.py          # daemon wiring with a scripted listener
  test_cli.py             # exit codes and argument wiring
```

Data flow — keep it this shape:

```python
img  = capture.grab_desktop()      # RAM only
box  = overlay.SelectionOverlay(img).run()  # None on Esc
if box is None: return             # clipboard untouched
crop = capture.crop(img, box)      # 1:1 pixels, no scaling
toks = backend.read_tokens(crop)   # list[Token]
text = layout.reconstruct(toks)
if text.strip():
    clipboard.copy(text)
```

### Token model

```python
@dataclass(frozen=True)
class Token:
    text: str
    left: float   # px relative to the crop
    top: float
    width: float
    height: float
    conf: float = 1.0
```

A token is a **word** (Windows, Tesseract) or a **phrase/line** (Vision). `layout.py` must stay
agnostic to which: never assume `token.text` has no spaces, and never split it.

## Layout algorithm — reference implementation contract

Implement exactly this in `layout.py`. It is the heart of the project; do not improvise around it.

1. **Filter** tokens with `conf < threshold` and whitespace-only text.
2. **Cluster into lines** by vertical overlap > 50% of the smaller height; sort by `top`; never merge
   across a gap larger than the median line height.
3. **Estimate metrics** from tokens with `len(text) >= 3`:
   - `char_width` = median of `width / len(text)`
   - `line_height` = median token height
   - `min_left` = min left edge of line-initial tokens → column 0
4. **Indent** each line: `round((first.left - min_left) / char_width)` leading spaces.
5. **Intra-line gaps**: for consecutive tokens, `max(1, round(gap / char_width))` spaces, capped
   (~40) so a stray detection cannot produce a wall of spaces.
6. **Blank lines**: when `next.top - prev.bottom > 1.5 * line_height`, insert
   `min(round(gap / line_height) - 1, 5)` empty lines.
7. **Emit** `"\n".join(line.rstrip() for line in lines)`, trailing blank lines stripped, no trailing
   newline.

Required edge-case behaviour:

- No tokens → `""` (caller then leaves the clipboard alone).
- **Single line → no indentation at all.** With no column 0 to compare against, a one-line copy of
  an indented line must not gain phantom leading spaces.
- Tabs: spaces are the default. `--indent tabs` (Phase 4) quantises **leading** indentation to
  `--tab-width` stops; internal gaps stay spaces. Never split a token to do it.
- Dark mode needs no special handling — native engines binarize themselves.
- Rotated/vertical text is out of scope; document, don't half-support.

## Platform rules

### macOS

- The process running the script needs **Screen Recording** permission. If the grab is
  near-uniform (all black or one flat colour), assume the permission is missing, print the exact
  settings path (*System Settings → Privacy & Security → Screen Recording*), and exit without
  copying.
- Vision **must** run with `setUsesLanguageCorrection_(False)`. Language correction mangles code:
  `def foo ()`, `self . x`, `x == 1` → `x = 1`. Do not "improve" this.
- Vision boxes are **normalised, origin bottom-left**:
  `left = bb.x * W`, `top = (1 - bb.y - bb.h) * H`.
- Capture uses **ScreenCaptureKit** (`SCScreenshotManager`, macOS 13+) and produces backing-store
  pixels: a 1470×956-point display yields a 2940×1912 image. Convert via `NSBitmapImageRep` → PNG
  bytes in `BytesIO` → PIL. Do not write an image file, even for debugging.
- PyObjC completion blocks **must return `None`**; returning anything (even from a `lambda`) raises
  inside libobjc and can terminate the process. Import `Quartz` before a `CGImage` callback runs so
  pyobjc registers the type.
- `selection_state.py` derives `image.width / viewport.width` only to map pointer points onto the
  preview. The crop is always in image pixels; never use that factor to "correct" the capture or the
  OCR tokens.
- `mss` depends on `CGWindowListCreateImage`, deprecated on recent macOS. It remains as a fallback
  when ScreenCaptureKit is unavailable; `capture.grab_desktop()` also falls back to
  `screencapture -x -t png -R … -` on a blank frame. Both paths stay disk-free.
- A blank capture means *permission denied* **or** *display asleep/locked*. The hint must mention
  both, or you will send users chasing a permission that is already granted.
- The overlay is a native borderless AppKit window at `kCGStatusWindowLevel` (high enough to cover
  apps, low enough that system dialogs and Force Quit stay reachable), with a flipped `NSView`,
  dimmed frozen image, bright selection, cyan outline and size label. It is *not* Tk.
  Cancel on Escape, right-click, window-close, global mouse-up outside the window, auto-close (test
  hook) and zero-area/tiny drags. `NSRunLoopCommonModes` is required for timers to fire during
  `runModalForWindow_`. PyObjC 13 has no `-[NSEvent locationInView:]`; pass `locationInWindow`
  through `convertPoint:fromView:` and trust its flipped result — flipping y again inverts the drag.
  Every event handler is guarded so an exception cancels the modal loop instead of trapping the user.

### Windows

- Call `ctypes.windll.shcore.SetProcessDpiAwareness(2)` **before** Tk is initialised, wrapped in
  `try/except` (older Windows). With per-monitor DPI awareness, Tk coords and `mss` pixels agree
  1:1 — do not add a scaling factor.
- `Windows.Media.Ocr` requires an installed OCR language pack; `stt doctor` should report what's
  available rather than failing at capture time.

## Coding standards

- Python 3.12+, full type annotations on every function (`list[Token]`, `tuple[int, int, int, int]`,
  `| None`).
- `pathlib` over `os.path`; `dataclasses` for data; no mutable default arguments.
- Keep `main.py` thin: parse args, wire modules, handle exit codes. Logic belongs in the module it
  belongs to.
- Errors the user can act on (permissions, missing language pack, no Tesseract binary) must be one
  clear line on stderr with the fix. No stack traces for expected failures.
- No new dependency without a reason that survives review: the whole point is a small, fast tool
  with no system-level install steps on macOS.

## Testing requirements

- `test_layout.py` is the primary safety net and must be written **before or with** `layout.py`.
  Cover: nested Python code with exact leading-space counts, a two-column table, blank-line
  insertion, single-line input (no phantom indent), `conf` filtering, empty input, `rstrip` and
  trailing-newline behaviour.
- `test_layout_render.py` renders known code with a real monospace font (Menlo/Consolas) and asserts
  the OCR round trip is **exact**; skip cleanly when no backend is installed.
- `test_selection_state.py` checks drag→pixel mapping at 1× and 2× with no window.
- `test_overlay.py` drives the real Tk overlay (Windows path) with synthesised mouse events and
  covers clamping and both cancel paths. `test_overlay_macos.py` does the same for the native AppKit
  overlay on macOS. Skip when there is no display.
- `test_grab_integration.py` fakes only the desktop image and the clipboard, then asserts the real
  overlay → crop → OCR → layout → copy chain reproduces the source exactly.
- `test_capture.py` asserts no files are created and that `crop()` is 1:1 (output size equals box
  size — this guards the no-scaling invariant).
- Run `uv run pytest` before declaring any phase done.

## Build plan (work in this order)

Phases 0, 1, 2 and 4 are done (92 tests green); the macOS capture/overlay rewrite is implemented.
Phase 3 (Windows) and the Windows `.exe` build are written but unverified.

| Phase | Work | Done when |
| --- | --- | --- |
| 0 | `pyproject.toml` (uv + hatchling), src layout, extras `[mac] [win] [tesseract]`, `stt doctor` skeleton | `uv sync --extra mac` clean, `stt --help` works |
| 1 | `models.py`, `layout.py`, `tests/test_layout.py` | `pytest` green on all layout cases |
| 2 | `capture.py`, `overlay.py`, `ocr/vision.py`, `clipboard.py`, `main.py` | macOS: select code, paste, diff vs original = empty; no files created |
| 3 | `ocr/windows.py`, DPI awareness, per-display overlay | verified on real Windows hardware |
| 4 | `pynput` hotkey daemon, `--indent tabs`, PyInstaller/py2app bundle | hotkey works without a terminal; `.app`/`.exe` runs (macOS `.app` builds; Windows `.exe` unverified) |

Do not skip ahead to packaging or hotkeys while Phase 1 tests are red.

## Known open items

- **Windows verification:** without Windows hardware, `ocr/windows.py` ships marked "unverified" in
  the README. Ask the user before claiming Windows support works.
- **Multi-monitor:** v1 targets the primary display only. ScreenCaptureKit already enumerates all
  displays; per-display AppKit windows positioned in global coordinate space are Phase 2.5. Verify
  negative window offsets empirically before designing around them.
- **No success feedback** is intentional; a 1-second HUD remains a possible opt-in future option,
  off by default (not shipped in Phase 4).

## When in doubt

Ask. The two things that must never be compromised are **indentation fidelity** and **clipboard
purity** — everything else is negotiable.
