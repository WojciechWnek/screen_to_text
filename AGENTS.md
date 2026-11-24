# AGENTS.md

Instructions for AI agents working in this repository. Read this before touching code.
`README.md` is the human-facing spec; this file is the operational rules. If they disagree, fix
whichever is wrong in the same change — do not leave them inconsistent.

## Project Overview

A lightweight cross-platform (macOS + Windows) desktop utility in Python. The user triggers a
capture, drags a rectangle over the screen, and the text inside that rectangle is OCR'd with
**indentation, internal spacing and blank lines preserved**, then copied to the clipboard as plain
text — nothing else. The screenshot is never written to disk.

Status: **Phases 0 and 1 are complete** — scaffold, pure layout engine and the `stt` CLI
(`grab`, `layout`, `doctor`) exist, with 31 tests green. Screen capture, the selection overlay and
the OCR backends are **Phase 2**; `stt grab` deliberately exits 1 with a "not wired up yet" notice.
Start there.

## Locked decisions (do not re-litigate these without asking the user)

These were chosen deliberately, with the trade-offs already weighed. Reversing any of them is a
scope change, not a refactor.

1. **Native OCR backends, no mandatory external binaries.**
   - macOS: Apple **Vision** (`VNRecognizeTextRequest`, `.accurate`) via `pyobjc`.
   - Windows: **`Windows.Media.Ocr`** via `winsdk` (returns true word-level boxes).
   - Optional fallback: **Tesseract** via `pytesseract`, behind the `[tesseract]` extra only.
   - All behind one `OcrBackend` protocol returning `list[Token]`.
2. **Frozen-desktop overlay, NOT a translucent tint.**
   Grab the desktop into memory *first*, then display that image in a borderless fullscreen Tk
   window with a dimming veil and a Canvas selection rectangle. On mouse release,
   `image.crop(box)` — the box is already in captured-image pixels.
   - This is why there is **no Retina/DPI scaling math anywhere**. Do not reintroduce coordinate
     conversion between Tk points and capture pixels.
   - This is why the captured image is never darkened by the overlay.
   - Do not "simplify" this back into an alpha overlay over the live screen.
3. **`layout.py` is a pure function.** `list[Token] -> str`. No I/O, no Tk, no OCR imports, no
   global state, no config file reads. All of the hard logic lives here and is tested without OCR.
4. **Tkinter** for the overlay (no Qt, no webview). **`mss`** for capture. **`pyperclip`** for the
   clipboard. **`uv`** for environments, pinned to **Python 3.12 or 3.13** — the machine's default
   3.15 is too new for reliable `pyobjc` wheels.
5. **CLI-first.** Entry point `stt`. Global hotkey daemon (`pynput`) and packaging are Phase 4, not
   Phase 1. Do not add a tray icon or daemon before the core works.

## Hard invariants

Violating any of these is a bug, regardless of what else works.

### 1. No files on disk, ever

- `mss` frames go straight to `PIL.Image.frombytes(...)`. No temp files, no `.png`, no `/tmp`, not
  even behind a debug flag.
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
- Always destroy the Tk root in a `finally` block.
- Bind both `<Escape>` and window-close. Catch `KeyboardInterrupt`.
- Releasing the mouse outside the window, or a zero-area drag, must be treated as a cancel.

### 4. Silence on success

No toast, no dialog, no terminal banner on the happy path. The text appearing on the clipboard *is*
the feedback. `--debug` is the only chatty mode.

## Architecture

```text
src/screen_ocr/
  main.py         # CLI: `stt`, `stt grab`, `stt doctor`; owns wiring, no logic
  models.py       # Token dataclass
  capture.py      # grab_desktop() -> PIL.Image, crop(img, box) -> PIL.Image
  overlay.py      # frozen-desktop selection UI, returns box|None
  layout.py       # PURE: list[Token] -> str
  clipboard.py    # copy(text), silent no-op on blank
  ocr/base.py     # OcrBackend protocol
  ocr/vision.py   # macOS
  ocr/windows.py  # Windows
  ocr/tesseract.py
tests/
  test_layout.py          # synthetic boxes -> golden strings (primary safety net)
  test_layout_render.py   # render real code with a real monospace font, round-trip
  test_capture.py         # crop invariants + "no files created"
```

Data flow — keep it this shape:

```python
img  = capture.grab_desktop()      # RAM only
box  = overlay.select(img)         # None on Esc
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
- Tabs: v1 emits spaces only. `--indent tabs` is Phase 4.
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
- `mss` depends on `CGWindowListCreateImage`, deprecated on recent macOS. Keep capture behind
  `grab_desktop()`; if it returns black frames, fall back to
  `screencapture -x -t png -` piped to stdout → PIL. That fallback must also not write files.

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
- `test_layout_render.py` renders known code with a real monospace font (Menlo/Consolas) and runs it
  through whichever backend is available; skip cleanly when none is installed so CI stays green.
- `test_capture.py` asserts no files are created and that `crop()` is 1:1 (output size equals box
  size — this guards the no-scaling invariant).
- Run `uv run pytest` before declaring any phase done.

## Build plan (work in this order)

Phases 0 and 1 are done (31 tests green); Phase 2 is next.

| Phase | Work | Done when |
| --- | --- | --- |
| 0 | `pyproject.toml` (uv + hatchling), src layout, extras `[mac] [win] [tesseract]`, `stt doctor` skeleton | `uv sync --extra mac` clean, `stt --help` works |
| 1 | `models.py`, `layout.py`, `tests/test_layout.py` | `pytest` green on all layout cases |
| 2 | `capture.py`, `overlay.py`, `ocr/vision.py`, `clipboard.py`, `main.py` | macOS: select code, paste, diff vs original = empty; no files created |
| 3 | `ocr/windows.py`, DPI awareness, per-display overlay | verified on real Windows hardware |
| 4 | `pynput` hotkey daemon, `--indent tabs`, PyInstaller/py2app bundle | hotkey works without a terminal; `.app`/`.exe` runs |

Do not skip ahead to packaging or hotkeys while Phase 1 tests are red.

## Known open items

- **Windows verification:** without Windows hardware, `ocr/windows.py` ships marked "unverified" in
  the README. Ask the user before claiming Windows support works.
- **Multi-monitor:** v1 targets the primary display only. Per-display overlay windows (Quartz
  `CGGetActiveDisplayList`, each positioned in global coordinate space) are Phase 2.5. Tk makes
  negative window offsets awkward — verify empirically on the Mac before designing around it.
- **No success feedback** is intentional; a 1-second HUD would be opt-in, Phase 4, off by default.

## When in doubt

Ask. The two things that must never be compromised are **indentation fidelity** and **clipboard
purity** — everything else is negotiable.
