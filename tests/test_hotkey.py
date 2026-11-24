"""Hotkey daemon wiring, with pynput replaced by a scripted listener."""

from __future__ import annotations

import sys

import pytest

from screen_ocr.hotkey import MAC_HOTKEY, WINDOWS_HOTKEY, default_hotkey, serve


class FakeListener:
    def __init__(self, hotkeys: dict[str, object]) -> None:
        self.hotkeys = hotkeys
        self.started = False
        self.stopped = False
        self.callback = next(iter(hotkeys.values()))

    def start(self) -> None:
        self.started = True
        # Simulate one hotkey press as soon as the listener is live.
        self.callback()

    def stop(self) -> None:
        self.stopped = True


def test_default_hotkey_matches_platform() -> None:
    expected = MAC_HOTKEY if sys.platform == "darwin" else WINDOWS_HOTKEY

    assert default_hotkey() == expected


def test_serve_runs_action_on_hotkey_and_stops_listener() -> None:
    listeners: list[FakeListener] = []
    calls: list[int] = []

    def factory(hotkeys: dict[str, object]) -> FakeListener:
        listener = FakeListener(hotkeys)
        listeners.append(listener)
        return listener

    code = serve(
        hotkey="<cmd>+<shift>+2",
        action=lambda: calls.append(1) or 0,
        listener_factory=factory,  # type: ignore[arg-type]
        quiet=True,
        max_actions=1,
    )

    assert code == 0
    assert calls == [1]
    assert listeners[0].started is True
    assert listeners[0].stopped is True


def test_serve_survives_an_action_error() -> None:
    calls: list[int] = []
    listeners: list[FakeListener] = []

    def factory(hotkeys: dict[str, object]) -> FakeListener:
        listener = FakeListener(hotkeys)
        listeners.append(listener)
        return listener

    def flaky() -> int:
        calls.append(1)
        if len(calls) == 1:
            listeners[0].callback()  # type: ignore[operator]
            raise RuntimeError("capture exploded")
        return 0

    code = serve(
        action=flaky,
        listener_factory=factory,  # type: ignore[arg-type]
        quiet=True,
        max_actions=2,
    )

    assert code == 0
    assert len(calls) == 2


def test_serve_stops_on_the_quit_hotkey() -> None:
    calls: list[int] = []
    quit_key = "<cmd>+<shift>+<alt>+2"

    def factory(hotkeys: dict[str, object]) -> FakeListener:
        quit_callback = hotkeys[quit_key]

        class QuitListener(FakeListener):
            def start(self) -> None:
                self.started = True
                quit_callback()  # type: ignore[operator]

        return QuitListener(hotkeys)

    code = serve(
        action=lambda: calls.append(1) or 0,
        listener_factory=factory,  # type: ignore[arg-type]
        quiet=True,
        quit_hotkey=quit_key,
    )

    assert code == 0
    assert calls == []


def test_serve_uses_the_registered_key() -> None:
    seen: dict[str, object] = {}

    def factory(hotkeys: dict[str, object]) -> FakeListener:
        seen.update(hotkeys)
        return FakeListener(hotkeys)

    serve(
        hotkey="<ctrl>+<alt>+9",
        action=lambda: 0,
        listener_factory=factory,  # type: ignore[arg-type]
        quiet=True,
        max_actions=1,
    )

    assert list(seen) == ["<ctrl>+<alt>+9"]


@pytest.mark.parametrize("style", ["spaces", "tabs"])
def test_listen_command_wires_grab_options(monkeypatch: pytest.MonkeyPatch, style: str) -> None:
    from screen_ocr import main as app

    captured: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("screen_ocr.hotkey.serve", fake_serve)

    assert app.main(["listen", "--quiet", "--indent", style]) == 0
    assert captured["quiet"] is True
    assert callable(captured["action"])
