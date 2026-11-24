# AGENTS.md

## Project Overview
This project is a lightweight, cross-platform (Windows & macOS) desktop utility written in Python. It allows a user to trigger a global hotkey or run a command to select a rectangular region on their screen, captures an in-memory screenshot (no files saved to disk), performs Optical Character Recognition (OCR) while **strictly preserving indentation, whitespace, and line breaks**, and copies *only* the extracted plain text directly to the system clipboard.

## Core Functional Requirements
1. **Cross-Platform Compatibility**: Must run seamlessly on both Windows (PC) and macOS.
2. **Interactive Region Selection**: Displays a translucent, full-screen overlay (supporting multi-monitor setups) where the user can click and drag a bounding box to define the capture area. Pressing `Esc` cancels the operation.
3. **In-Memory Screen Capture ("Imaginary Screenshot")**: Captures the selected coordinates directly into memory (e.g., `PIL.Image` or `numpy` array) without writing temporary image files to the user's storage.
4. **Layout & Indentation-Aware OCR**: Extracts text from the captured image while preserving leading spaces, tabs, and relative horizontal alignment (crucial for code snippets, terminal outputs, or structured lists).
5. **Clean Clipboard Output**: Copies *only* the raw extracted text to the OS clipboard—no trailing notifications, metadata, or formatting wrappers—ready to be pasted anywhere via `Ctrl+V` / `Cmd+V`.

---

## Recommended Tech Stack
- **Language**: Python 3.10+
- **GUI / Region Selection Overlay**: `tkinter` (built-in, lightweight, zero extra heavy dependencies) or `PyQt6` / `PySide6` (if high-DPI multi-monitor scaling proves tricky in `tkinter`).
- **Screen Capture**: `mss` (ultra-fast, cross-platform, handles multi-monitor coordinates cleanly, returns in-memory buffers).
- **Image Preprocessing**: `Pillow` (PIL) & `opencv-python-headless` / `numpy` (for grayscale conversion, upscaling small text, and thresholding to improve OCR accuracy).
- **OCR Engine**:
  - *Primary Recommendation*: `pytesseract` (wrapper for Tesseract OCR) using `--psm 6` (Assume a single uniform block of text) or `--psm 4` + custom bounding-box reconstruction (`image_to_data`) to calculate exact X-coordinate indentation offsets.
  - *Alternative (Zero-binary setup)*: `rapidocr-onnxruntime` or `easyocr` (note: requires custom bounding-box-to-indentation sorting logic).
- **Clipboard Management**: `pyperclip` (cross-platform clipboard read/write).
- **Global Hotkey (Optional Background Daemon Mode)**: `pynput` or `keyboard`.

---

## Architecture & Directory Structure
Keep the codebase modular so UI, capture, OCR layout reconstruction, and OS-specific quirks are cleanly isolated:

```text
screen-text-grabber/
├── AGENTS.md               # Project context and agent instructions
├── pyproject.toml          # Dependencies and build configuration
├── README.md               # Setup instructions (including Tesseract & macOS permissions)
└── src/
    └── screen_ocr/
        ├── __init__.py
        ├── main.py         # Entry point (CLI & optional hotkey listener)
        ├── overlay.py      # Full-screen translucent snipping UI & coordinate selector
        ├── capture.py      # In-memory screen grabbing via `mss`
        ├── ocr_engine.py   # Image preprocessing + indentation-preserving OCR logic
        └── clipboard.py    # Clean clipboard injection
```

---

## Critical Implementation Guidelines for AI Agents

### 1. Preserving Indentation & Whitespace (High Priority)
Standard OCR often strips leading whitespace. To guarantee indentation is preserved:
- **Do not rely solely on `image_to_string` default trimming.**
- Use `pytesseract.image_to_data(..., output_type=Output.DICT)` to retrieve bounding boxes (`left`, `top`, `width`, `height`, `text`, `line_num`, `block_num`).
- **Algorithm for Indentation Reconstruction**:
  1. Group detected words by line (using `block_num`, `par_num`, `line_num`, or clustering similar `top` Y-coordinates).
  2. Find the minimum `left` X-coordinate across all lines in the selection (`min_x`) to serve as the left margin baseline (`0` indentation).
  3. Estimate average character width (`avg_char_width`) from the bounding boxes of detected words (`width / len(text)`).
  4. For each line, calculate leading spaces: `indent_spaces = round((line_first_word_left - min_x) / avg_char_width)`.
  5. Reconstruct gaps between words on the same line similarly so internal spacing/columns are also respected.
  6. Strip trailing whitespace per line, preserve empty lines if vertical gaps exceed `1.5 * median_line_height`, and do **not** add extra conversational text.

### 2. Platform-Specific Nuances (PC vs. Mac)
- **macOS Retina / High-DPI Scaling**:
  - `tkinter` reports logical screen coordinates, while `mss` captures physical pixels (often `2x` on Retina displays).
  - Agents must detect the display scale factor (or compare `mss` monitor dimensions against `tkinter` screen dimensions) and scale selection coordinates accordingly before cropping/capturing.
- **macOS Permissions**:
  - Screen capture on macOS requires **Screen Recording** permission in *System Settings > Privacy & Security > Screen Recording*.
  - Global hotkeys (if implemented via `pynput`) require **Accessibility** permissions.
  - Handle permission denials gracefully with a clear console error message.
- **Windows DPI Awareness**:
  - On Windows, call `ctypes.windll.shcore.SetProcessDpiAwareness(2)` (or `1`) before initializing `tkinter` so multi-monitor coordinates with mixed scaling (e.g., 100% and 150%) do not drift.

### 3. In-Memory Rule ("Imaginary Screenshot")
- **NEVER** write `.png` or `.jpg` files to disk or `/tmp`.
- Pass `mss` raw BGRA frames directly into `PIL.Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")`.
- Perform upscaling (e.g., `2x` Lanczos resize for small UI fonts) and grayscale/contrast enhancement strictly in RAM.

### 4. Clipboard Purity
- Ensure `pyperclip.copy(extracted_text)` receives **only** the extracted string.
- If OCR detects no text (e.g., user selected a blank area), do not overwrite the user's existing clipboard contents; exit silently or log to stderr.

---

## Coding Standards & Verification
- **Type Hints**: Use Python 3.10+ type annotations (`list[str]`, `tuple[int, int, int, int]`, etc.) across all functions.
- **Error Handling**: Catch `KeyboardInterrupt` and `Esc` key presses cleanly so the overlay never locks the user's screen. Always ensure the overlay window is destroyed (`root.destroy()`) *before* taking the screenshot so the translucent tint does not darken the captured image.
- **Testing**:
  - Write unit tests in `tests/test_ocr_layout.py` using synthetic PIL images rendered with monospace and proportional fonts containing nested Python code (e.g., `def`, `if/else` blocks) to verify exact leading space preservation.