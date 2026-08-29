from __future__ import annotations

import pytest

from hermes_computer_bridge.input_protocol import (
    BUTTONS,
    char_to_keysym,
    input_calls,
)

NODE = 52


def test_move_is_absolute_against_the_stream_node():
    calls = input_calls({"op": "move", "x": 100, "y": 200}, NODE)
    assert calls == [("NotifyPointerMotionAbsolute", (NODE, 100.0, 200.0))]


def test_click_moves_then_presses_and_releases():
    calls = input_calls({"op": "click", "x": 10, "y": 20, "button": "left"}, NODE)
    assert calls == [
        ("NotifyPointerMotionAbsolute", (NODE, 10.0, 20.0)),
        ("NotifyPointerButton", (BUTTONS["left"], 1)),
        ("NotifyPointerButton", (BUTTONS["left"], 0)),
    ]


def test_click_without_coordinates_clicks_in_place():
    calls = input_calls({"op": "click", "button": "right"}, NODE)
    assert calls == [
        ("NotifyPointerButton", (BUTTONS["right"], 1)),
        ("NotifyPointerButton", (BUTTONS["right"], 0)),
    ]


def test_button_press_and_release_are_separable_for_drag():
    press = input_calls({"op": "button", "button": "left", "state": "press"}, NODE)
    release = input_calls({"op": "button", "button": "left", "state": "release"}, NODE)
    assert press == [("NotifyPointerButton", (BUTTONS["left"], 1))]
    assert release == [("NotifyPointerButton", (BUTTONS["left"], 0))]


def test_every_named_button_maps_to_a_distinct_evdev_code():
    assert BUTTONS["left"] == 272
    assert BUTTONS["right"] == 273
    assert BUTTONS["middle"] == 274


def test_scroll_passes_both_axes():
    calls = input_calls({"op": "scroll", "dx": 0.0, "dy": -3.0}, NODE)
    assert calls == [("NotifyPointerAxis", (0.0, -3.0))]


def test_text_types_each_character_press_then_release():
    calls = input_calls({"op": "text", "text": "hi"}, NODE)
    assert calls == [
        ("NotifyKeyboardKeysym", (char_to_keysym("h"), 1)),
        ("NotifyKeyboardKeysym", (char_to_keysym("h"), 0)),
        ("NotifyKeyboardKeysym", (char_to_keysym("i"), 1)),
        ("NotifyKeyboardKeysym", (char_to_keysym("i"), 0)),
    ]


def test_ascii_keysym_is_the_code_point():
    assert char_to_keysym("a") == 0x61
    assert char_to_keysym(" ") == 0x20
    assert char_to_keysym("Z") == 0x5A


def test_a_named_key_taps_by_default():
    calls = input_calls({"op": "key", "key": "Return"}, NODE)
    assert calls == [
        ("NotifyKeyboardKeysym", (0xFF0D, 1)),
        ("NotifyKeyboardKeysym", (0xFF0D, 0)),
    ]


def test_a_key_chord_wraps_the_key_in_its_modifiers():
    calls = input_calls({"op": "key", "key": "c", "mods": ["ctrl"]}, NODE)
    assert calls == [
        ("NotifyKeyboardKeysym", (0xFFE3, 1)),
        ("NotifyKeyboardKeysym", (0x63, 1)),
        ("NotifyKeyboardKeysym", (0x63, 0)),
        ("NotifyKeyboardKeysym", (0xFFE3, 0)),
    ]


def test_an_unknown_op_is_rejected():
    with pytest.raises(ValueError):
        input_calls({"op": "teleport"}, NODE)


def test_a_missing_op_is_rejected():
    with pytest.raises(ValueError):
        input_calls({"x": 1, "y": 2}, NODE)


def test_drag_presses_at_the_start_moves_then_releases_at_the_end():
    calls = input_calls(
        {"op": "drag", "from_x": 10, "from_y": 20, "to_x": 100, "to_y": 200, "button": "left"},
        NODE,
    )
    assert calls == [
        ("NotifyPointerMotionAbsolute", (NODE, 10.0, 20.0)),
        ("NotifyPointerButton", (BUTTONS["left"], 1)),
        ("NotifyPointerMotionAbsolute", (NODE, 100.0, 200.0)),
        ("NotifyPointerButton", (BUTTONS["left"], 0)),
    ]
