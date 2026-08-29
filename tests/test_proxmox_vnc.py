from __future__ import annotations

import struct

from hermes_computer_bridge.proxmox_client import vncwebsocket_uri
from hermes_computer_bridge.rfb_client import (
    fb_update_request,
    set_encodings_msg,
    set_pixel_format_msg,
    vnc_auth_response,
    vnc_key,
)


def test_websocket_uri_is_wss_with_port_and_ticket():
    uri = vncwebsocket_uri("https://host:8006", "pve", 112, 5900, "tick et/+=")
    assert uri.startswith("wss://host:8006/api2/json/nodes/pve/qemu/112/vncwebsocket?")
    assert "port=5900" in uri
    assert "vncticket=tick+et%2F%2B%3D" in uri


def test_vnc_key_reverses_the_bits_of_each_byte_padded_to_eight():
    assert vnc_key(b"\x01") == bytes([0x80]) + b"\x00" * 7
    assert len(vnc_key(b"abcdefghij")) == 8


def test_vnc_auth_response_is_16_bytes_and_deterministic():
    challenge = bytes(range(16))
    first = vnc_auth_response(challenge, b"secret")
    second = vnc_auth_response(challenge, b"secret")
    assert len(first) == 16
    assert first == second
    assert vnc_auth_response(challenge, b"other") != first


def test_set_pixel_format_is_twenty_bytes_of_32bpp_truecolor():
    msg = set_pixel_format_msg()
    assert len(msg) == 20
    assert msg[0] == 0
    bpp, depth, big_endian, true_colour = msg[4], msg[5], msg[6], msg[7]
    assert (bpp, depth, big_endian, true_colour) == (32, 24, 0, 1)


def test_set_encodings_lists_raw():
    msg = set_encodings_msg([0])
    assert msg[0] == 2
    count = struct.unpack(">H", msg[2:4])[0]
    assert count == 1
    assert struct.unpack(">i", msg[4:8])[0] == 0


def test_framebuffer_update_request_carries_the_full_rect():
    msg = fb_update_request(0, 0, 0, 1280, 800)
    kind, incremental, x, y, w, h = struct.unpack(">BBHHHH", msg)
    assert kind == 3
    assert (incremental, x, y, w, h) == (0, 0, 0, 1280, 800)
