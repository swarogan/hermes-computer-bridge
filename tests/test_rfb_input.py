from __future__ import annotations

import struct

import pytest

from hermes_computer_bridge.input_protocol import char_to_keysym, key_to_keysym
from hermes_computer_bridge.rfb_input import (
    RFB_BUTTON,
    RfbInput,
    key_event,
    pointer_event,
)


def test_pointer_event_is_type5_mask_x_y_big_endian():
    assert pointer_event(1, 300, 200) == struct.pack(">BBHH", 5, 1, 300, 200)


def test_key_event_is_type4_down_pad_keysym_big_endian():
    assert key_event(True, 0xFF0D) == struct.pack(">BBHI", 4, 1, 0, 0xFF0D)
    assert key_event(False, 0x61) == struct.pack(">BBHI", 4, 0, 0, 0x61)


def test_move_tracks_position_with_the_current_button_mask():
    rfb = RfbInput()
    assert rfb.encode({"op": "move", "x": 10, "y": 20}) == [pointer_event(0, 10, 20)]


def test_click_presses_then_releases_at_the_point():
    rfb = RfbInput()
    out = rfb.encode({"op": "click", "x": 5, "y": 6, "button": "left"})
    assert out == [
        pointer_event(RFB_BUTTON["left"], 5, 6),
        pointer_event(0, 5, 6),
    ]


def test_button_press_holds_the_mask_for_the_next_move():
    rfb = RfbInput()
    rfb.encode({"op": "move", "x": 1, "y": 1})
    press = rfb.encode({"op": "button", "button": "left", "state": "press"})
    move = rfb.encode({"op": "move", "x": 2, "y": 2})
    assert press == [pointer_event(RFB_BUTTON["left"], 1, 1)]
    assert move == [pointer_event(RFB_BUTTON["left"], 2, 2)]


def test_scroll_down_pulses_the_wheel_bit():
    rfb = RfbInput()
    out = rfb.encode({"op": "scroll", "dx": 0, "dy": 3})
    assert out == [pointer_event(16, 0, 0), pointer_event(0, 0, 0)]


def test_drag_holds_the_button_from_start_to_end():
    rfb = RfbInput()
    out = rfb.encode(
        {"op": "drag", "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4, "button": "left"}
    )
    bit = RFB_BUTTON["left"]
    assert out == [
        pointer_event(0, 1, 2),
        pointer_event(bit, 1, 2),
        pointer_event(bit, 3, 4),
        pointer_event(0, 3, 4),
    ]


def test_text_types_each_char_down_then_up_with_x_keysyms():
    rfb = RfbInput()
    out = rfb.encode({"op": "text", "text": "hi"})
    assert out == [
        key_event(True, char_to_keysym("h")),
        key_event(False, char_to_keysym("h")),
        key_event(True, char_to_keysym("i")),
        key_event(False, char_to_keysym("i")),
    ]


def test_key_chord_wraps_the_key_in_modifiers():
    rfb = RfbInput()
    out = rfb.encode({"op": "key", "key": "c", "mods": ["ctrl"]})
    assert out == [
        key_event(True, key_to_keysym("ctrl")),
        key_event(True, key_to_keysym("c")),
        key_event(False, key_to_keysym("c")),
        key_event(False, key_to_keysym("ctrl")),
    ]


def test_unknown_op_is_rejected():
    with pytest.raises(ValueError):
        RfbInput().encode({"op": "teleport"})


def test_text_wraps_shifted_symbols_in_shift():
    from hermes_computer_bridge.rfb_input import SHIFT_KEYSYM
    out = RfbInput().encode({"op": "text", "text": "_"})
    ks = char_to_keysym("_")
    assert out == [
        key_event(True, SHIFT_KEYSYM),
        key_event(True, ks),
        key_event(False, ks),
        key_event(False, SHIFT_KEYSYM),
    ]
