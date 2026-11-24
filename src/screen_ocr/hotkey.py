"""Global hotkey daemon.

The hotkey callback runs on pynput's listener thread, but Tk (and therefore the
overlay) must be driven from the main thread on macOS. The listener therefore
only pushes an event into a queue; :func:`serve` runs the actual grab on the
main thread.
"""

from __future__ import annotations

import queue
import sys
from collections.abc import Callable
from typing import Protocol

MAC_HOTKEY = "<cmd>+<shift>+2"
WINDOWS_HOTKEY = "<ctrl>+<shift>+2"
MAC_QUIT_HOTKEY = "<cmd>+<shift>+<alt>+2"
WINDOWS_QUIT_HOTKEY = "<ctrl>+<shift>+<alt>+2"


class HotkeyUnavailable(RuntimeError):
    """Raised when pynput cannot provide a global listener."""


class Listener(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


ListenerFactory = Callable[[dict[str, Callable[[], None]]], Listener]


def default_hotkey() -> str:
    """The platform-appropriate default hotkey."""
    return MAC_HOTKEY if sys.platform == "darwin" else WINDOWS_HOTKEY


def default_quit_hotkey() -> str:
    """Hotkey that stops a bundled daemon with no terminal to Ctrl+C."""
    return MAC_QUIT_HOTKEY if sys.platform == "darwin" else WINDOWS_QUIT_HOTKEY


def create_listener(hotkeys: dict[str, Callable[[], None]]) -> Listener:
    """Build a pynput ``GlobalHotKeys`` listener, lazily importing pynput."""
    try:
        from pynput import keyboard
    except ImportError as exc:
        raise HotkeyUnavailable(
            "pynput is not installed; run: uv sync --extra hotkey"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - pynput can fail at import time
        raise HotkeyUnavailable(f"cannot load pynput: {exc}") from exc

    try:
        return keyboard.GlobalHotKeys(hotkeys)
    except Exception as exc:  # noqa: BLE001 - surface one actionable line
        raise HotkeyUnavailable(f"cannot register the hotkey: {exc}") from exc


def _default_action() -> int:
    from .main import grab_once

    return grab_once()


def serve(
    *,
    hotkey: str | None = None,
    action: Callable[[], int] | None = None,
    listener_factory: ListenerFactory | None = None,
    quiet: bool = False,
    max_actions: int | None = None,
    quit_hotkey: str | None = None,
) -> int:
    """Block, running ``action`` once per hotkey press.

    ``Ctrl+C`` stops the daemon. ``max_actions`` exists for tests; production
    callers leave it as ``None``. Returns the last action's exit code, or 0 on
    a clean interrupt.
    """
    key = hotkey or default_hotkey()
    events: queue.Queue[str] = queue.Queue()
    factory: ListenerFactory = listener_factory or create_listener

    def on_trigger() -> None:
        events.put("grab")

    def on_quit() -> None:
        events.put("quit")

    hotkeys: dict[str, Callable[[], None]] = {key: on_trigger}
    if quit_hotkey:
        hotkeys[quit_hotkey] = on_quit
    listener = factory(hotkeys)
    listener.start()
    if not quiet:
        print(f"listening for {key} (Ctrl+C to stop)", file=sys.stderr)

    busy = False
    last_code = 0
    handled = 0
    try:
        while max_actions is None or handled < max_actions:
            event = events.get()
            if event == "quit":
                return 0
            if busy:
                # A second press while an overlay is open is just ignored; two
                # Tk selections at once would be a mess.
                continue
            busy = True
            try:
                last_code = (action or _default_action)()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - daemon must survive errors
                print(f"error: {exc}", file=sys.stderr)
                last_code = 1
            finally:
                busy = False
                handled += 1
    except KeyboardInterrupt:
        return 0
    finally:
        listener.stop()
    return last_code
