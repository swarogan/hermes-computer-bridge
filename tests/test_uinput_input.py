from __future__ import annotations

import pytest

from hermes_computer_bridge.uinput_input import (
    BUTTON,
    KEY_LEFTSHIFT,
    UinputInput,
    char_to_keycode,
    key_to_keycode,
)


class FakeDevice:
    def __init__(self) -> None:
        self.calls: list = []

    def move_norm(self, nx: float, ny: float) -> None:
        self.calls.append(("move", round(nx, 3), round(ny, 3)))

    def button(self, code: int, down: bool) -> None:
        self.calls.append(("button", code, down))

    def scroll(self, ticks: int) -> None:
        self.calls.append(("scroll", ticks))

    def key(self, code: int, down: bool) -> None:
        self.calls.append(("key", code, down))


def _service(fake):
    return UinputInput(frame_path=None, device_factory=lambda: fake)


def test_char_to_keycode_handles_case_and_symbols():
    assert char_to_keycode("a") == (30, False)
    assert char_to_keycode("A") == (30, True)
    assert char_to_keycode("1") == (2, False)
    assert char_to_keycode("_") == (12, True)
    assert char_to_keycode(" ") == (57, False)


def test_key_to_keycode_names_and_modifiers():
    assert key_to_keycode("Return") == 28
    assert key_to_keycode("ctrl") == 29
    assert key_to_keycode("a") == 30
    with pytest.raises(ValueError):
        key_to_keycode("Nonsense")


def test_click_moves_absolute_then_presses_and_releases():
    fake = FakeDevice()
    _service(fake).inject({"op": "click", "x": 0.5, "y": 0.25, "button": "left"})
    assert fake.calls == [
        ("move", 0.5, 0.25),
        ("button", BUTTON["left"], True),
        ("button", BUTTON["left"], False),
    ]


def test_text_wraps_uppercase_and_symbols_in_shift():
    fake = FakeDevice()
    _service(fake).inject({"op": "text", "text": "A"})
    assert fake.calls == [
        ("key", KEY_LEFTSHIFT, True),
        ("key", 30, True),
        ("key", 30, False),
        ("key", KEY_LEFTSHIFT, False),
    ]


def test_key_chord_wraps_the_key_in_modifiers():
    fake = FakeDevice()
    _service(fake).inject({"op": "key", "key": "c", "mods": ["ctrl"]})
    assert fake.calls == [
        ("key", 29, True),
        ("key", 46, True),
        ("key", 46, False),
        ("key", 29, False),
    ]


def test_scroll_moves_the_wheel():
    fake = FakeDevice()
    _service(fake).inject({"op": "scroll", "dx": 0, "dy": 3})
    assert fake.calls == [("scroll", -1)]
