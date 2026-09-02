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


# --- Polish programmer layout over standard RFB KeyEvent ---

ISO_LEVEL3_SHIFT = 0xFE03


def test_altgr_chord_sends_iso_level3_shift_around_the_key():
    out = RfbInput().encode({"op": "key", "key": "a", "mods": ["altgr"]})
    assert out == [
        key_event(True, ISO_LEVEL3_SHIFT),
        key_event(True, 0x61),
        key_event(False, 0x61),
        key_event(False, ISO_LEVEL3_SHIFT),
    ]


def test_polish_letters_are_typed_as_unicode_keysyms_not_alt_chords():
    out = RfbInput().encode({"op": "text", "text": "ąęłśźżćń"})
    expected = []
    for ch in "ąęłśźżćń":
        ks = 0x01000000 | ord(ch)
        expected += [key_event(True, ks), key_event(False, ks)]
    assert out == expected


def test_unicode_keysym_is_a_four_byte_big_endian_field():
    assert key_event(True, char_to_keysym("ł")) == struct.pack(">BBHI", 4, 1, 0, 0x01000142)


def test_latin1_polish_o_acute_stays_on_its_legacy_keysym():
    out = RfbInput().encode({"op": "text", "text": "ó"})
    assert out == [key_event(True, 0xF3), key_event(False, 0xF3)]


def test_polish_letters_are_not_wrapped_in_shift():
    from hermes_computer_bridge.rfb_input import SHIFT_KEYSYM

    out = RfbInput().encode({"op": "text", "text": "ą"})
    assert key_event(True, SHIFT_KEYSYM) not in out


# --- QEMU Extended Key Event (pseudo-encoding -258) ---------------------------
#
# Plain KeyEvent makes QEMU translate a keysym through its own keymap, which has
# no entry for Unicode keysyms like 'ą' (0x01000105) — the keystroke is dropped.
# The extension carries the physical key instead, so the guest's own layout turns
# AltGr+a into 'ą'.


def test_qemu_key_event_wire_format():
    from hermes_computer_bridge.rfb_input import qemu_key_event

    # type 255, submessage 0, down-flag u16, keysym u32, keycode u32
    assert qemu_key_event(True, 0x61, 0x1E) == struct.pack(">BBHII", 255, 0, 1, 0x61, 0x1E)
    assert qemu_key_event(False, 0x61, 0x1E) == struct.pack(">BBHII", 255, 0, 0, 0x61, 0x1E)


def test_extended_key_sends_the_physical_scancode_not_the_unicode_keysym():
    from hermes_computer_bridge.rfb_input import qemu_key_event

    rfb = RfbInput()
    rfb.qemu_ext_key = True
    out = rfb.encode({"op": "key", "code": "KeyA", "key": "ą", "state": "press"})
    assert out == [qemu_key_event(True, char_to_keysym("ą"), 0x1E)]


def test_extended_key_release_uses_the_same_scancode():
    from hermes_computer_bridge.rfb_input import qemu_key_event

    rfb = RfbInput()
    rfb.qemu_ext_key = True
    out = rfb.encode({"op": "key", "code": "AltRight", "key": "AltGraph", "state": "release"})
    assert out == [qemu_key_event(False, key_to_keysym("AltGraph"), 0xB8)]


def test_extended_key_without_state_presses_and_releases():
    from hermes_computer_bridge.rfb_input import qemu_key_event

    rfb = RfbInput()
    rfb.qemu_ext_key = True
    out = rfb.encode({"op": "key", "code": "Enter", "key": "Return"})
    assert out == [
        qemu_key_event(True, key_to_keysym("Return"), 0x1C),
        qemu_key_event(False, key_to_keysym("Return"), 0x1C),
    ]


def test_falls_back_to_plain_key_event_when_the_server_lacks_the_extension():
    rfb = RfbInput()
    assert rfb.qemu_ext_key is False
    out = rfb.encode({"op": "key", "code": "KeyA", "key": "a", "state": "press"})
    assert out == [key_event(True, char_to_keysym("a"))]


def test_falls_back_when_the_code_has_no_scancode():
    rfb = RfbInput()
    rfb.qemu_ext_key = True
    out = rfb.encode({"op": "key", "code": "NoSuchKey", "key": "a", "state": "press"})
    assert out == [key_event(True, char_to_keysym("a"))]


def test_agent_commands_without_a_code_still_use_plain_key_events():
    rfb = RfbInput()
    rfb.qemu_ext_key = True
    out = rfb.encode({"op": "key", "key": "Return"})
    assert out == [key_event(True, 0xFF0D), key_event(False, 0xFF0D)]


def test_extended_scancodes_are_recoded_to_the_high_bit_form():
    """QEMU wants extended keys as 0x80|low, not as the raw 0xe0-prefixed XT code.

    Sent raw, AltRight (0xe038) and ControlRight (0xe01d) reach the guest as
    nothing at all: measured on vm:113, AltGr+a produced 'a' and Ctrl+a failed to
    select, while non-extended keys worked. noVNC does the same recoding.
    """
    from hermes_computer_bridge.rfb_input import qemu_key_event, rfb_keycode

    assert rfb_keycode(0xE038) == 0xB8, "AltRight"
    assert rfb_keycode(0xE01D) == 0x9D, "ControlRight"
    assert rfb_keycode(0x1E) == 0x1E, "non-extended codes pass through"

    rfb = RfbInput()
    rfb.qemu_ext_key = True
    out = rfb.encode({"op": "key", "code": "AltRight", "key": "altgr", "state": "press"})
    assert out == [qemu_key_event(True, key_to_keysym("altgr"), 0xB8)]
