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

> **Status: planned, not yet implemented.** This document is the spec and the build plan.

---

## Contents

- [Why this is not just "Tesseract + a snipping tool"](#why-this-is-not-just-tesseract--a-snipping-tool)
- [Key design decisions](#key-design-decisions)
- [Architecture](#architecture)
- [The layout reconstruction algorithm](#the-layout-reconstruction-algorithm)
- [Installation](#installation)
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

The obvious approach — a 25%-alpha fullscreen window over the live screen, then map Tk coordinates
to physical pixels — is the classic source of two bugs: the tint ends up baked into the capture, and
Retina/DPI scaling drifts (Tk reports points, captures are pixels).

Instead:

1. Grab the desktop into memory **first** (`PIL.Image`, via `mss`).
2. Show that image in a borderless fullscreen Tk window with a dimming veil and a Canvas selection
   rectangle.
3. On mouse release, `img.crop(box)` — the rectangle is *already* in captured-image pixels.

This gives us, for free:

- **zero coordinate/scale math** — no 2× Retina correction anywhere
- **no tint in the output** — pixels were grabbed before anything was drawn
- **the "imaginary screenshot" rule holds trivially** — the image never leaves RAM
- **one window, one code path** — no per-monitor geometry gymnastics on the happy path

Cost: the full desktop is grabbed even if you press `Esc` (~10 ms, tens of MB, freed immediately).

### 3. Layout reconstruction is a pure function

`layout.py` knows nothing about screenshots, threads, Tk, or OCR engines. It takes
`list[Token]` and returns `str`. That makes the hardest part of the project **exactly testable with
no OCR flakiness** — synthetic boxes in, expected text out.

### 4. Zero disk writes, ever

- `mss` frames → `PIL.Image.frombytes(...)` directly.
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
├── pyproject.toml             # uv / hatchling, extras: [mac] [win] [tesseract]
├── src/
│   └── screen_ocr/
│       ├── __init__.py
│       ├── main.py            # CLI entry point: `stt`, `stt grab`, `stt doctor`
│       ├── models.py          # Token(text, left, top, width, height, conf)
│       ├── capture.py         # desktop grab + crop, all in RAM
│       ├── overlay.py         # frozen-desktop selection UI (Tk + Canvas)
│       ├── layout.py          # geometry → indented text  (PURE, no I/O)
│       ├── clipboard.py       # text-only injection, silent on empty
│       └── ocr/
│           ├── __init__.py    # backend selection by platform / --engine
│           ├── base.py        # OcrBackend protocol
│           ├── vision.py      # macOS Vision via pyobjc
│           ├── windows.py     # Windows.Media.Ocr via winsdk
│           └── tesseract.py   # optional fallback
└── tests/
    ├── test_layout.py         # synthetic boxes, golden strings
    ├── test_layout_render.py  # render real code to PIL, round-trip
    └── test_capture.py        # scale/crop invariants, no disk artifacts
```

Data flow, precisely:

```python
img  = capture.grab_desktop()                    # mss → PIL, RAM only
box  = overlay.select(img)                       # None if Esc
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
- Tabs: v1 emits spaces. `--indent tabs` (phase 4) quantizes indents to tab stops when the source
  clearly used them.
- Dark mode (light text on dark background) is free — the native engines do their own binarization.
- Vertical text / rotated text: out of scope, documented.

---

## Installation

Requires **Python 3.12 or 3.13** (pinned in the venv; the machine's default 3.15 is too new for
reliable `pyobjc` / `onnxruntime` wheels).

```bash
# macOS
brew install uv                       # if you don't have it
uv sync --extra mac

# Windows (PowerShell)
uv sync --extra win
```

Optional Tesseract fallback:

```bash
uv sync --extra tesseract
brew install tesseract                # macOS
winget install UB-Mannheim.TesseractOCR  # Windows
```

---

## Usage

```bash
uv run stt              # select a region → text is on your clipboard
uv run stt --debug      # print the reconstructed text + token boxes instead of copying
uv run stt doctor       # check permissions, engine availability, DPI scale, monitor layout
```

Then paste anywhere with `Cmd+V` / `Ctrl+V`.

Bind `stt` to a keyboard shortcut with the tools you already have — no background daemon, no
Accessibility permission:

- **macOS**: Raycast / Alfred / Automator Quick Action → `uv run stt` in this directory.
  A native `pynput` hotkey daemon is phase 4.
- **Windows**: a shortcut's *Target* set to the launcher `.exe`, or AutoHotkey.

`--debug` is the recommended way to tune the layout: it prints the reconstructed text between
markers plus the raw token table, so you can see exactly why a column collapsed.

---

## Platform notes

### macOS

- **Screen Recording** permission is required by whatever process runs the script (Terminal, iTerm,
  the packaged app). Path:
  *System Settings → Privacy & Security → Screen Recording*.
- If the grabbed desktop is near-uniform (all black / one flat colour), the README-level diagnosis
  is "permission missing" — the tool prints that exact path instead of copying garbage.
- Retina is a non-issue by design (see *Frozen-desktop overlay*), because selection coordinates
  never leave the captured-image pixel space.
- `mss` relies on `CGWindowListCreateImage`, which is deprecated on recent macOS. Capture is
  behind a single `grab_desktop()` function; if `mss` starts returning black frames, the fallback
  is `screencapture -x -t png -` piped to stdout → PIL (still zero disk writes). Swapping is a
  ~5-line change.

### Windows

- `ctypes.windll.shcore.SetProcessDpiAwareness(2)` is called **before** Tk initialises, so mixed
  100%/150% multi-monitor setups don't drift. With per-monitor DPI awareness, Tk coordinates and
  `mss` pixels agree 1:1 — no scaling factor needed.
- `Windows.Media.Ocr` needs a language pack with OCR support; `stt doctor` reports what's available.

---

## Build plan

| Phase | Deliverable | Done when |
| --- | --- | --- |
| **0** | `pyproject.toml` (uv + hatchling), src layout, extras, `stt doctor` skeleton | `uv sync --extra mac` is clean, `stt --help` works |
| **1** | `models.py`, `layout.py`, `tests/test_layout.py` | `pytest` green on nested Python code, blank lines, table columns, single-line and empty input |
| **2** | `capture.py`, `overlay.py`, `ocr/vision.py`, `clipboard.py`, `main.py` | On macOS: select code in an editor, paste, diff against the original, no files created |
| **3** | `ocr/windows.py`, DPI awareness, per-display overlay | Verified on a real Windows machine (see [open items](#open-items)) |
| **4** | `pynput` hotkey daemon, `--indent tabs`, PyInstaller / py2app bundle | Hotkey grabs without a terminal; `stt.app` / `stt.exe` runs |

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

**Render round-trip (`tests/test_layout_render.py`).** Draw known code with a real monospace font
(`Menlo` / `Consolas`) via Pillow, run it through whichever backend is available, assert the text
matches. This catches engine-specific surprises (e.g. Vision's phrase splitting). Skipped
automatically when no backend is installed so CI stays green.

**Manual acceptance.** Open a source file with 3+ indentation levels, select it, paste into an
editor with soft-wrap disabled, and diff. Target: zero diffs on monospace code.

**Invariant test.** `test_capture.py` asserts the tool creates no files: run a capture in a
temporary cwd and assert the directory is still empty afterwards.

---

## Open items

1. **Windows hardware.** Without a Windows machine to test phase 3, that backend ships as
   "written but unverified" and is flagged as such in this README. Say the word if you have one.
2. **Multi-monitor.** v1 targets the primary display. Multi-display support (one overlay window per
   display, enumerated via Quartz `CGGetActiveDisplayList`, each positioned in the global coordinate
   space) is a phase 2.5 item — the one part of `AGENTS.md` that Tk makes genuinely awkward. The
   frozen-desktop design keeps the *coordinates* trivial; only window placement is fiddly.
3. **Toolbar-less UX.** After a successful copy there is deliberately **no** notification. If that
   feels like the tool didn't run, a 1-second tray-less HUD is a possible phase-4 option — off by
   default.

---

## Licence

MIT.
