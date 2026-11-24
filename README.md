# screen-text-grab

Select a rectangle on your screen → get the text inside it **as text**, with indentation and line
breaks intact, already on your clipboard. No files, no preview window, no "copy" button. Just paste.

Cross-platform: **macOS** (Apple Silicon / Intel) and **Windows**.

```
┌──────────────────────────────────────────────────────────┐
│  Ctrl/Cmd+Shift+2  (or:  uv run stt)                     │
│            │                                             │
│            ▼                                             │
│  desktop frozen to memory → drag a box → Esc cancels     │
│            │                                             │
│            ▼                                             │
│  native OCR:  macOS Vision  /  Windows.Media.Ocr         │
│            │                                             │
│            ▼                                             │
│  indentation reconstruction (pure Python, unit-tested)   │
│            │                                             │
│            ▼                                             │
│  clipboard: text only, nothing else                      │
└──────────────────────────────────────────────────────────┘
```

> **Status: Phases 0–2 and 4 complete; macOS capture/overlay rewritten.** `uv run stt` works
> end-to-end on macOS: ScreenCaptureKit grabs the desktop into memory, a native AppKit overlay lets
> you drag a box, Apple Vision reads it, indentation is reconstructed, and the text lands on your
> clipboard. 92 tests cover the layout engine, capture invariants, both selection overlays, the full
> pipeline and the hotkey daemon. Windows still uses `mss` + Tk + `Windows.Media.Ocr` unchanged;
> Phase 3 remains **unverified** on real hardware.

---

## Contents

- [Why this is not just "Tesseract + a snipping tool"](#why-this-is-not-just-tesseract--a-snipping-tool)
- [Key design decisions](#key-design-decisions)
- [Architecture](#architecture)
- [The layout reconstruction algorithm](#the-layout-reconstruction-algorithm)
- [Installation](#installation)
- [Building the bundles](#building-the-bundles)
- [Usage](#usage)
- [Platform notes](#platform-notes)
- [Build plan](#build-plan)
- [Testing](#testing)
- [Open items](#open-items)

---

## Why this is not just "Tesseract + a snipping tool"

Default OCR APIs return a blob of text with newlines guessed at and **leading whitespace destroyed**.
That is fine for prose and useless for:

- a Python / C / Rust snippet with nested `if` blocks
- terminal output, `git diff`, logs
- a two-column table
- YAML, JSON, Markdown lists

This tool reconstructs the 2D layout from OCR **word geometry** (bounding boxes) and re-emits it as
monospace-flavoured plain text: leading spaces from each line's x-offset, spaces for intra-line gaps,
blank lines from vertical gaps. Then it copies *only* that string.

## Key design decisions

### 1. Native OCR engines, no external binaries

| Platform | Engine | Why |
| --- | --- | --- |
| macOS | `Vision` (`VNRecognizeTextRequest`, accurate) via `pyobjc` | Best-in-class on UI/small/code text, ships with the OS, no install step |
| Windows | `Windows.Media.Ocr` via `winsdk` | Ships with Windows 10+, gives true **word-level** bounding boxes |
| any | Tesseract via `pytesseract` (optional extra) | Fallback / cross-check, requires `brew install tesseract` or the Windows installer |

All three are hidden behind one tiny interface (`OcrBackend` → `list[Token]`), so the rest of the
program does not know or care which one ran.

### 2. Frozen-desktop overlay, not a translucent tint

The obvious approach — a translucent window over the live screen, then map UI coordinates to
physical pixels — is the classic source of two bugs: the tint ends up baked into the capture, and
Retina/DPI scaling drifts (the toolkit reports points, captures are pixels).

Instead:

1. Grab the desktop into memory **first**:
   - macOS: ScreenCaptureKit → `CGImage` → `PIL.Image` (backing-store pixels).
   - Windows: `mss` → `PIL.Image` (nominal pixels).
2. Show a frozen preview with a dimming veil and a selection rectangle:
   - macOS: a borderless full-screen **AppKit** window.
   - Windows: a full-screen **Tk** canvas.
3. On mouse release, `img.crop(box)` — the rectangle is *already* in captured-image pixels.

This gives us:

- **no scaling in the crop path** — `selection_state.py` maps the drag once; the caller just crops
- **no tint in the output** — pixels were grabbed before anything was drawn
- **the "imaginary screenshot" rule holds trivially** — the image never leaves RAM
- **one interaction model on both OSes** — Esc / tiny drag / close all cancel

ScreenCaptureKit captures at backing resolution (a 1470×956-point Mac display yields a 2940×1912
image). The native overlay derives `image.width / window.width` **only** to draw the preview; the
crop stays in image pixels. That is the sole scale factor in the project, and it never touches the
saved text.

Cost: the full desktop is grabbed even if you press `Esc` (tens of MB, freed immediately).

### 3. Layout reconstruction is a pure function

`layout.py` knows nothing about screenshots, threads, Tk, or OCR engines. It takes
`list[Token]` and returns `str`. That makes the hardest part of the project **exactly testable with
no OCR flakiness** — synthetic boxes in, expected text out.

### 4. Zero disk writes, ever

- ScreenCaptureKit frames → `NSBitmapImageRep` → PNG bytes in `BytesIO` → `PIL.Image`.
- `mss` frames → `PIL.Image.frombytes(...)` directly (Windows and fallback).
- Preprocessing (upscale, grayscale, contrast) in RAM only.
- No `.png`/`.jpg`, no temp directories, not even for debugging. `--debug` prints **text** to
  stdout instead of copying.
- Clipboard gets the string and nothing else — no metadata, no footer, no filename.

---

## Architecture

```text
screen_to_text/
├── agents.md                  # agent instructions for this repo
├── README.md                  # this file
├── pyproject.toml             # uv / hatchling, extras: [mac] [win] [tesseract] [hotkey] [bundle]
├── src/
│   └── screen_ocr/
│       ├── __init__.py
│       ├── main.py            # CLI: `stt`, `stt grab`, `stt listen`, `stt demo`, `stt doctor`
│       ├── models.py          # Token(text, left, top, width, height, conf)
│       ├── capture.py         # ScreenCaptureKit/mss grab + crop, all in RAM
│       ├── overlay.py         # overlay API + Windows Tk implementation
│       ├── overlay_macos.py   # native AppKit/Quartz selection window
│       ├── selection_state.py # pure drag-point → image-pixel mapping
│       ├── layout.py          # geometry → indented text  (PURE, no I/O)
│       ├── clipboard.py       # text-only injection, silent on empty
│       ├── hotkey.py          # pynput daemon; queues events, grabs on the main thread
│       ├── launcher.py        # bundle entry point: starts `listen --quiet`
│       └── ocr/
│           ├── __init__.py    # backend selection by platform / --engine
│           ├── base.py        # OcrBackend protocol
│           ├── vision.py      # macOS Vision via pyobjc
│           ├── windows.py     # Windows.Media.Ocr via winsdk
│           └── tesseract.py   # optional fallback
├── packaging/
│   ├── py2app_setup.py        # macOS .app build
│   └── stt.spec               # Windows .exe build
├── scripts/
│   ├── build_macos.sh
│   └── build_windows.ps1
└── tests/
    ├── test_layout.py         # synthetic boxes, golden strings
    ├── test_layout_render.py  # render real code to PIL, round-trip
    ├── test_capture.py        # scale/crop invariants, no disk artifacts
    ├── test_overlay.py        # Windows/Tk selection UI, both cancel paths
    ├── test_overlay_macos.py  # native AppKit selection UI and cancel paths
    ├── test_selection_state.py  # pure drag → pixel geometry
    ├── test_grab_integration.py  # full overlay → OCR → clipboard chain
    ├── test_hotkey.py         # daemon wiring with a scripted listener
    └── test_cli.py            # exit codes and argument wiring
```

Data flow, precisely:

```python
img  = capture.grab_desktop()                    # SCK (mac) / mss (win) → PIL, RAM only
box  = overlay.SelectionOverlay(img).run()        # None if Esc
if box is None: return                           # clipboard untouched
crop = capture.crop(img, box)                    # pure crop, 1:1 pixels
toks = backend.read_tokens(crop)                 # list[Token], engine-specific
text = layout.reconstruct(toks, char_width=..., line_height=...)
if text.strip():
    clipboard.copy(text)                         # text only
```

### The `Token` model

```python
@dataclass(frozen=True)
class Token:
    text: str
    left: float      # px, relative to the crop
    top: float
    width: float
    height: float
    conf: float = 1.0
```

A token is **either a word or a whole phrase**, depending on the engine:

- `windows.py` — one token per word (the OCR API returns word rects) → best fidelity, including
  multi-column tables.
- `vision.py` — one token per observation, which Vision returns per *line/phrase*. Wide gaps
  (table columns, `key: value` pairs) usually arrive as separate observations, so columns still
  reconstruct. Text *within* a token keeps its original single spaces.
- `tesseract.py` — word level via `image_to_data(..., output_type=DICT)`.

`layout.py` is token-agnostic, so all three work without special cases.

---

## The layout reconstruction algorithm

Input: `list[Token]`, all coordinates relative to the selection.
Output: a `str` with no trailing newline.

1. **Filter.** Drop tokens with `conf < threshold` and tokens whose text is whitespace-only.

2. **Cluster into lines.** Sort by `top`. Two tokens share a line if their vertical spans overlap by
   more than 50% of the smaller height (robust against italics, subscripts, and uneven baselines).
   Never merge across a gap larger than the median line height.

3. **Estimate the metrics.** From tokens with `len(text) >= 3`:
   - `char_width` = median of `width / len(text)` — stable on monospace code, good enough on
     proportional text.
   - `line_height` = median token height.
   - `min_left` = smallest left edge across all line-initial tokens → this is **column 0**.

4. **Indentation.** For each line, take its first token:
   `indent = round((first.left - min_left) / char_width)` → that many leading spaces.

5. **Intra-line gaps.** Sort tokens in the line by `left`; for each neighbouring pair,
   `spaces = max(1, round((next.left - (prev.left + prev.width)) / char_width))`, capped at a sane
   maximum (e.g. 40) so a stray detection far to the right cannot produce a wall of spaces.

6. **Blank lines.** Between consecutive lines, if
   `next.top - prev.bottom > 1.5 * line_height`, insert
   `min(round(gap / line_height) - 1, 5)` empty lines.

7. **Emit.** `"\n".join(line.rstrip() for line in lines)`.
   Trailing whitespace per line is stripped; trailing blank lines are stripped; no trailing newline.

**Edge cases handled explicitly**

- Empty selection / no tokens → return `""` → caller leaves the clipboard alone.
- A single line → no indentation applied (there is no column 0 to compare against), so a one-line
  copy of an indented line does not gain phantom leading spaces.
- Tabs: spaces are the default. `--indent tabs` quantizes **leading** indentation to tab stops
  (`--tab-width`, default 4); internal gaps stay spaces. A single selected line is never indented.
- Dark mode (light text on dark background) is free — the native engines do their own binarization.
- Vertical text / rotated text: out of scope, documented.

---

## Installation

Requires **Python 3.12 or 3.13** (pinned in the venv; the machine's default 3.15 is too new for
reliable `pyobjc` wheels). The native capture path needs **macOS 13+** for ScreenCaptureKit; older
Macs fall back to `mss` + `screencapture`.

```bash
# macOS
brew install uv                       # if you don't have it
uv sync --extra mac --extra hotkey    # --extra hotkey adds the `stt listen` daemon (optional)

# Windows (PowerShell)
uv sync --extra win --extra hotkey
```

Optional Tesseract fallback:

```bash
uv sync --extra tesseract
brew install tesseract                # macOS
winget install UB-Mannheim.TesseractOCR  # Windows
```

---

## Building the bundles

Both scripts install the `hotkey` and `bundle` extras for you and start the app straight into the
hotkey daemon (no terminal window).

```bash
# macOS → dist/stt.app
./scripts/build_macos.sh

# Windows (PowerShell) → dist\stt.exe
.\scripts\build_windows.ps1
```

The `.app` is built with py2app and the `.exe` with PyInstaller. On macOS, a rebuilt bundle is a
**new app** to the permission system: re-grant Screen Recording and Accessibility, then relaunch it.
The Windows build config is written but, like the Windows OCR backend, has not been verified on real
hardware from this machine.

If you only want the hotkey from source, skip bundling and run `uv run stt listen`.

---

## Usage

```bash
uv run stt                            # drag a box → its text is on your clipboard
uv run stt listen                     # global hotkey daemon: Cmd/Ctrl+Shift+2 to grab
uv run stt grab --indent tabs         # leading indentation as tab stops (internal gaps stay spaces)
uv run stt grab --debug               # print the reconstructed text + token boxes instead of copying
uv run stt demo                       # render a code sample and OCR it (no screen, no permissions)
uv run stt doctor                     # permissions, OCR engines, displays, DPI sanity check
```

Then paste anywhere with `Cmd+V` / `Ctrl+V`.

On success there is **no** output at all — the text appearing on your clipboard is the feedback.
Exit codes: `0` copied, `1` error (permission, no text, engine failure), `2` cancelled with `Esc`.

The daemon prints one startup line to stderr, then stays silent. Press **Cmd/Ctrl+Shift+2** to grab;
**Cmd/Ctrl+Shift+Alt+2** quits it. Both are configurable with `--hotkey` and `--quit-hotkey`.

### Verify it without touching the screen

`stt demo` renders a code snippet with a real monospace font, runs the actual OCR engine over it,
reconstructs the layout, and tells you whether it matched the source byte for byte:

```bash
uv run stt demo
```

```text
def process(items):
    for item in items:
        if item.active:
            results.append(item)
    return results

[exact match, 5 tokens, vision]
```

This is the quickest way to tell whether an OCR engine or a preprocessing change helped or hurt.

### Try the layout engine in isolation

Feed it token geometry as JSON and it prints the reconstructed text:

```bash
uv run stt layout examples/sample_tokens.json
```

Add `--show-tokens` to see the parsed bounding boxes on stderr, or pipe JSON on stdin:

```bash
echo '[{"text":"a","left":0,"top":0,"width":9,"height":18},
       {"text":"b","left":90,"top":0,"width":9,"height":18}]' | uv run stt layout
```

### Hotkey permissions

`stt listen` uses `pynput` for the global hotkey. On macOS the process needs
**System Settings → Privacy & Security → Accessibility** (Input Monitoring may also be requested).
Grant it to your terminal before starting the daemon, then restart the terminal — macOS applies the
change only to newly started processes. Screen Recording is still required for the capture itself.

If you would rather not grant Accessibility, bind `uv run stt` in Raycast / Alfred / Automator
(macOS) or point a shortcut at `stt.exe` (Windows).

`--debug` is the recommended way to tune the layout: it prints the reconstructed text plus the raw
token table, so you can see exactly why a column collapsed.

---

## Platform notes

### macOS

- **Screen Recording** permission is required by whatever process runs the script (Terminal, iTerm,
  the packaged app). Path:
  *System Settings → Privacy & Security → Screen Recording*.
  `stt doctor` checks this and says `BLOCKED` if the capture comes back flat.
- A blank capture is ambiguous: it means either the permission is missing **or** the display is
  asleep/locked. The error message says both, rather than sending you on a permissions hunt when
  your Mac was simply idle.
- Capture uses **ScreenCaptureKit** (`SCScreenshotManager`, macOS 13+) and returns backing-store
  pixels — 2940×1912 for a 1470×956-point display. The image is converted to PIL through an
  in-memory PNG buffer; nothing is written to disk.
- The selection window is a native **AppKit** borderless full-screen window at status level (below
  system dialogs). It draws the frozen capture at full brightness, lays a single light veil (20%)
  over everything outside the selection, and shows the drag rectangle with its size. Esc,
  right-click, window-close, mouse-up outside the window and tiny drags all cancel; an
  event-handler error also closes it instead of trapping the desktop.
- **Retina is a non-issue for the crop.** `selection_state.py` derives
  `image.width / window.width` solely to map pointer points onto the preview. The box handed to
  `crop()` is already in image pixels; there is no second conversion.
- `mss` relies on `CGWindowListCreateImage`, which is deprecated on recent macOS. It remains as a
  fallback when ScreenCaptureKit is unavailable (older macOS), and the
  `screencapture -x -t png -R … -` fallback runs when a frame comes back blank. Both stay
  disk-free.
- Tk is not imported on macOS; the native overlay is used instead.

### Windows

- `ctypes.windll.shcore.SetProcessDpiAwareness(2)` is called **before** Tk initialises, so mixed
  100%/150% multi-monitor setups don't drift. With per-monitor DPI awareness, Tk coordinates and
  `mss` pixels agree 1:1 — no scaling factor needed.
- `Windows.Media.Ocr` needs a language pack with OCR support; `stt doctor` reports what's available.

---

## Build plan

| Phase | Deliverable | Done when | Status |
| --- | --- | --- | --- |
| **0** | `pyproject.toml` (uv + hatchling), src layout, extras, `stt doctor` skeleton | `uv sync --extra mac` is clean, `stt --help` works | ✅ |
| **1** | `models.py`, `layout.py`, `tests/test_layout.py` | `pytest` green on nested Python code, blank lines, table columns, single-line and empty input | ✅ |
| **2** | `capture.py`, `overlay.py`, `ocr/vision.py`, `clipboard.py`, `main.py` | On macOS: select code in an editor, paste, diff against the original, no files created | ✅ |
| **3** | `ocr/windows.py`, DPI awareness, per-display overlay | Verified on a real Windows machine (see [open items](#open-items)) | ⚠️ written, unverified |
| **4** | `pynput` hotkey daemon, `--indent tabs`, PyInstaller / py2app bundle | Hotkey grabs without a terminal; `stt.app` / `stt.exe` runs | ✅ implemented; macOS `.app` builds, Windows `.exe` config unverified |

Phases 0–1 need no GUI and no permissions, which is why they come first: verifiable progress on the
riskiest logic before anything touches the screen.

---

## Testing

**Unit (`tests/test_layout.py`) — the important one.** No OCR, no GUI, no permissions. Hand-built
token lists that mimic real geometry:

- nested Python code, verifying exact leading-space counts per level
- a two-column table, verifying intra-line gap widths
- blank-line insertion from vertical gaps
- `rstrip` and trailing-newline behaviour
- single-line selection → no phantom indent
- `conf` filtering
- empty input → `""`

**Render round-trip (`tests/test_layout_render.py`).** Draws known code with a real monospace font
(`Menlo` / `Consolas`) via Pillow, runs it through whichever backend is available, and asserts the
text matches **exactly**. Skipped automatically when no backend is installed.

**Overlay geometry (`tests/test_overlay.py`, `tests/test_overlay_macos.py`,
`tests/test_selection_state.py`).** The pure state tests check the point→pixel mapping at 1× and 2×
without a window. The Tk suite (Windows path) and the native AppKit suite (macOS path) each open a
real window and synthesise drags, clamping, Esc, window-close and tiny-drag cancels. Skipped when no
display is available.

**Full pipeline (`tests/test_grab_integration.py`).** Only the desktop image and the clipboard are
faked — a rendered snippet is pasted onto a screen-sized canvas, a scripted drag selects it, and the
real overlay → crop → Vision → layout → copy path has to reproduce the source byte for byte.

**Capture invariants (`tests/test_capture.py`).** `crop()` is 1:1 (output size equals box size),
macOS dispatch selects ScreenCaptureKit with an `mss` fallback, and a grab in a temporary cwd
leaves the directory empty.

**Manual acceptance.** Open a source file with 3+ indentation levels, select it with `uv run stt`,
paste into an editor with soft-wrap disabled, and diff. Target: zero diffs on monospace code.

---

## Open items

1. **Windows hardware.** `ocr/windows.py` is written against `winsdk`'s `Windows.Media.Ocr` but has
   never run on Windows. It is wrapped so that any failure degrades to
   `stt grab --engine tesseract` instead of crashing. Needs a real PC to call it done.
2. **Multi-monitor.** v1 captures and overlays the **primary display only**. ScreenCaptureKit already
   enumerates all displays; adding one native AppKit window per display (positioned in global
   coordinates) is a phase 2.5 item. The frozen-desktop design keeps the *coordinates* trivial;
   only window placement is fiddly.
3. **Hotkey verification.** `stt listen` and the bundled `.app` start cleanly, but the actual
   Cmd+Shift+2 grab needs Accessibility granted and a manual press on real hardware. If you would
   rather not grant it, bind `uv run stt` to a key in Raycast/Alfred/Automator (macOS) or a
   shortcut/AutoHotkey (Windows).
4. **Windows bundle.** `packaging/stt.spec` produces `dist\stt.exe` in theory, but the build has not
   been run on Windows (see item 1).
5. **Toolbar-less UX.** After a successful copy there is deliberately **no** notification. If that
   feels like the tool didn't run, an opt-in 1-second tray-less HUD remains a future option — off by
   default.

---

## Licence

MIT.
