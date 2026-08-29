"""Regressions for bugs found by actually running the step-2 stack.

Each of these passed unit tests before and still failed on the real machine.
"""

from pathlib import Path

import pytest

from hermes_computer_bridge.geometry import parse_kscreen_outputs, strip_ansi
from hermes_computer_bridge.portal_capture import PortalCapture
from hermes_computer_bridge.provider import Frame, StreamInfo

# Verbatim bytes from `kscreen-doctor -o` — it colourises even when piped.
KSCREEN_ANSI = (
    "Output: 1 DP-1 6e29c9d4\n"
    "\tenabled\n"
    "\x1b[01;33m\tGeometry: \x1b[0;0m0,0 2048x1152\n"
    "Output: 2 HDMI-A-1 591b9868\n"
    "\tenabled\n"
    "\x1b[01;33m\tGeometry: \x1b[0;0m2048,72 1920x1080\n"
)


def test_strip_ansi_removes_colour_codes():
    assert "\x1b[" not in strip_ansi(KSCREEN_ANSI)


def test_parse_survives_ansi_colour():
    """BUG: geometry came back empty because of escape codes."""
    outs = parse_kscreen_outputs(KSCREEN_ANSI)
    assert [o.name for o in outs] == ["DP-1", "HDMI-A-1"]
    assert (outs[1].x, outs[1].y) == (2048, 72)


PRETTY_JSON = """{
  "ok": true,
  "screencast": {
    "version": 5
  }
}"""

COMPACT_JSON = '{"ok": true, "path": "/tmp/a.png"}'


class _Proc:
    def __init__(self, stdout, code=0, stderr=""):
        self.stdout = stdout
        self.returncode = code
        self.stderr = stderr


def test_parses_pretty_printed_probe_output(monkeypatch):
    """BUG: parser only read the LAST line, so pretty JSON was invisible."""
    p = PortalCapture()
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: _Proc(PRETTY_JSON)
    )
    code, payload, _ = p._run(["probe"], timeout_s=5)
    assert code == 0
    assert payload["ok"] is True
    assert payload["screencast"]["version"] == 5


def test_still_parses_compact_single_line(monkeypatch):
    p = PortalCapture()
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc(COMPACT_JSON))
    _, payload, _ = p._run(["spike"], timeout_s=5)
    assert payload["path"] == "/tmp/a.png"


def test_parses_json_after_noise_lines(monkeypatch):
    p = PortalCapture()
    noisy = "bash: warning about job control\n" + COMPACT_JSON
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _Proc(noisy))
    _, payload, _ = p._run(["spike"], timeout_s=5)
    assert payload["ok"] is True


def test_probe_rejects_payload_without_version(monkeypatch):
    from hermes_computer_bridge.errors import CapabilityMissing

    p = PortalCapture()
    monkeypatch.setattr(p, "_preflight", lambda: None)
    monkeypatch.setattr(
        p, "_run", lambda args, timeout_s: (0, {"ok": True, "screencast": {}}, "")
    )
    with pytest.raises(CapabilityMissing, match="ScreenCast interface absent"):
        p.probe()


# -- blank frame ------------------------------------------------------


def _frame(stats):
    return Frame(
        path=Path("/tmp/x.png"),
        width=3968,
        height=1152,
        bytes_len=20696,
        stream=StreamInfo(node_id=275, width=3968, height=1152),
        rung="portal-screencast",
        stats=stats,
    )


def test_blank_frame_is_flagged():
    """BUG: first buffers off pipewiresrc were black and reported as success."""
    f = _frame({"available": True, "variance": 0.0, "unique_colors": 1, "blank": True})
    assert f.blank is True
    assert f.to_dict()["blank"] is True


def test_real_frame_not_flagged():
    f = _frame(
        {"available": True, "variance": 15038.37, "unique_colors": 188, "blank": False}
    )
    assert f.blank is False


def test_missing_stats_does_not_claim_blank():
    assert _frame({}).blank is False
