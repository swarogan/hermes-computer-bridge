"""Clipboard delivery: one packet instead of one keystroke per character.

Typing sends two RFB messages per character and the guest drops what it cannot
keep up with — measured as `ls` vanishing and `-R -n` arriving as `--`. Pasting
carries the whole string in a single ClientCutText, so there is no stream to
lose and the guest's keyboard layout stops mattering.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import struct
from pathlib import Path

import pytest

from hermes_computer_bridge.rfb_client import RfbClient
from hermes_computer_bridge.rfb_input import RfbInput

ROOT = Path(__file__).resolve().parent.parent


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def _client(previous_clipboard: str = ""):
    rfb = RfbClient.__new__(RfbClient)
    rfb._input = RfbInput()
    rfb._ws = FakeWs()
    rfb.clipboard = previous_clipboard
    return rfb


def _cut_texts(sent):
    """Every ClientCutText (type 6) payload, in order."""
    out = []
    for message in sent:
        if message[0] == 6:
            length = struct.unpack(">I", message[4:8])[0]
            out.append(message[8 : 8 + length].decode("latin-1"))
    return out


def test_whole_string_travels_in_one_packet():
    rfb = _client()
    asyncio.run(rfb.paste("pgrep -R -n hypridle"))
    assert _cut_texts(rfb._ws.sent) == ["pgrep -R -n hypridle"]


def test_paste_presses_shift_insert():
    """Shift+Insert pastes in terminals too; Ctrl+V is taken there."""
    rfb = _client()
    asyncio.run(rfb.paste("ls"))
    key_events = [m for m in rfb._ws.sent if m[0] == 4]
    keysyms = [struct.unpack(">BBHI", m)[3] for m in key_events]
    assert 0xFFE1 in keysyms, "Shift_L"
    assert 0xFF63 in keysyms, "Insert"


def test_the_guests_clipboard_is_handed_back():
    """A human copying in the VM must not find our command in its place."""
    rfb = _client(previous_clipboard="something the human copied")
    asyncio.run(rfb.paste("ls", settle_s=0))
    assert _cut_texts(rfb._ws.sent) == ["ls", "something the human copied"]


def test_nothing_is_restored_when_the_guest_clipboard_is_unknown():
    """AgentVnc never reads, so the previous contents are often unknown.

    Writing an empty string back would erase a clipboard we simply could not
    see — worse than leaving our text there.
    """
    rfb = _client(previous_clipboard="")
    asyncio.run(rfb.paste("ls", settle_s=0))
    assert _cut_texts(rfb._ws.sent) == ["ls"]


def test_restore_waits_for_the_guest_to_consume_the_paste():
    rfb = _client(previous_clipboard="prev")
    naps = []

    async def fake_sleep(seconds):
        naps.append(seconds)

    original, asyncio.sleep = asyncio.sleep, fake_sleep
    try:
        asyncio.run(rfb.paste("ls", settle_s=0.4))
    finally:
        asyncio.sleep = original
    assert 0.4 in naps


# --- the agent-facing tool ----------------------------------------------------


@pytest.fixture()
def plugin(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("cb_paste_under_test", ROOT / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_bindings_file", lambda: tmp_path / "bindings.json")
    (tmp_path / "bindings.json").write_text(json.dumps({"default": "vm:113"}))
    return module


def test_paste_tool_reaches_the_selected_vm(plugin, monkeypatch):
    calls = []

    class FakeVnc:
        def paste(self, info, text):
            calls.append((info["id"], text))
            return True

    monkeypatch.setattr(plugin, "_vnc", lambda: FakeVnc())
    body = plugin._paste({"text": "ls -la"})
    assert body["ok"] is True
    assert calls == [("vm:113", "ls -la")]
    assert body["pasted_chars"] == 6


def test_paste_marks_input_so_a_stale_frame_cannot_deny_it(plugin, monkeypatch):
    """Same rule as typing: the next look must not predate what we pasted."""
    class FakeVnc:
        def paste(self, info, text):
            return True

    monkeypatch.setattr(plugin, "_vnc", lambda: FakeVnc())
    plugin._LAST_INPUT_AT = 0.0
    plugin._paste({"text": "ls"})
    assert plugin._LAST_INPUT_AT > 0.0


def test_paste_refuses_the_local_desktop(plugin):
    body = plugin._paste({"text": "ls", "target": "local"})
    assert body["ok"] is False
    assert body["kind"] == "missing"


def test_paste_rejects_empty_text(plugin):
    assert plugin._paste({"text": ""})["ok"] is False


def test_failed_delivery_does_not_mark_input(plugin, monkeypatch):
    class DeadVnc:
        def paste(self, info, text):
            return False

    monkeypatch.setattr(plugin, "_vnc", lambda: DeadVnc())
    plugin._LAST_INPUT_AT = 0.0
    assert plugin._paste({"text": "ls"})["ok"] is False
    assert plugin._LAST_INPUT_AT == 0.0
