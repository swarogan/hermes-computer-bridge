"""Provider ABC + ladder integration, with no graphical session."""

import json
from pathlib import Path

import pytest

from hermes_computer_bridge.capture_service import CaptureService, NotImplementedRung
from hermes_computer_bridge.errors import (
    CapabilityMissing,
    TransientError,
    UserCancelled,
)
from hermes_computer_bridge.portal_capture import PortalCapture
from hermes_computer_bridge.provider import CaptureProvider, Frame, StreamInfo

PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "05fefe9d0f0000000049454e44ae426082"
)


def _stream(node_id=237, size=(3968, 1152), position=None, index=0):
    return StreamInfo(
        node_id=node_id,
        index=index,
        width=size[0] if size else None,
        height=size[1] if size else None,
        position=position,
    )


class FakeCapture(CaptureProvider):
    rung = "portal-screencast"

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def probe(self):
        if self.exc:
            raise self.exc
        return {"rung": self.rung, "screencast_version": 5}

    def capture(self, output, *, stream_index=None, node_id=None, timeout_s=180):
        self.calls.append({"stream_index": stream_index, "node_id": node_id})
        if self.exc:
            raise self.exc
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(PNG_1x1)
        s = _stream()
        return Frame(
            path=output,
            width=3968,
            height=1152,
            bytes_len=output.stat().st_size,
            stream=s,
            rung=self.rung,
            all_streams=[s],
        )


# -- StreamInfo ------------------------------------------------------


def test_streaminfo_from_helper_keeps_everything():
    s = StreamInfo.from_helper(
        {
            "index": 1,
            "node_id": 53,
            "size": [1920, 1080],
            "position": [2048, 72],
            "source_type": 1,
            "id": "HDMI-A-1",
            "props": {"size": [1920, 1080]},
        }
    )
    assert s.node_id == 53
    assert s.size == (1920, 1080)
    assert s.position == (2048, 72)
    assert s.id == "HDMI-A-1"


def test_streaminfo_position_stays_none_when_backend_silent():
    """KDE does not report position. Inventing (0,0) would be a lie."""
    s = StreamInfo.from_helper({"node_id": 237, "size": [3968, 1152]})
    assert s.position is None
    assert s.to_dict()["position"] is None


def test_streaminfo_handles_absent_size():
    s = StreamInfo.from_helper({"node_id": 9})
    assert s.size is None


# -- ladder behaviour -----------------------------------------------


def test_service_uses_portal_first():
    portal = FakeCapture()
    svc = CaptureService(providers={
        "portal-screencast": portal,
        "wlr-screencopy": NotImplementedRung("wlr-screencopy"),
        "x11-shm": NotImplementedRung("x11-shm"),
        "remote-rfb": NotImplementedRung("remote-rfb"),
    })
    frame, result = svc.capture(Path("/tmp/hdb-test-frame.png"))
    assert frame is not None
    assert result.used_rung == "portal-screencast"
    assert frame.path.is_file()


def test_unbuilt_rungs_report_missing_not_fake_success():
    svc = CaptureService(providers={
        "portal-screencast": NotImplementedRung("portal-screencast"),
        "wlr-screencopy": NotImplementedRung("wlr-screencopy"),
        "x11-shm": NotImplementedRung("x11-shm"),
        "remote-rfb": NotImplementedRung("remote-rfb"),
    })
    frame, result = svc.capture(Path("/tmp/hdb-none.png"))
    assert frame is None
    assert not result.ok
    assert {a.kind for a in result.attempts} == {"missing"}


def test_user_cancel_propagates_and_stops_ladder():
    svc = CaptureService(providers={
        "portal-screencast": FakeCapture(UserCancelled("dismissed")),
        "wlr-screencopy": NotImplementedRung("wlr-screencopy"),
        "x11-shm": FakeCapture(),
        "remote-rfb": NotImplementedRung("remote-rfb"),
    })
    frame, result = svc.capture(Path("/tmp/hdb-cancel.png"))
    assert frame is None
    # Must NOT silently fall through to X11 after the user said no.
    assert result.attempts[-1].kind == "cancelled"
    assert result.used_rung is None


def test_stream_selection_is_passed_through():
    portal = FakeCapture()
    svc = CaptureService(providers={
        "portal-screencast": portal,
        "wlr-screencopy": NotImplementedRung("wlr-screencopy"),
        "x11-shm": NotImplementedRung("x11-shm"),
        "remote-rfb": NotImplementedRung("remote-rfb"),
    })
    svc.capture(Path("/tmp/hdb-sel.png"), node_id=53)
    assert portal.calls[-1]["node_id"] == 53


# -- PortalCapture translation ---------------------------------------


def test_exit_code_cancelled_maps_to_usercancelled():
    p = PortalCapture()
    with pytest.raises(UserCancelled):
        p._raise_for(5, {"error": "user cancelled"}, "")


def test_exit_code_gi_missing_maps_to_capability_missing():
    p = PortalCapture()
    with pytest.raises(CapabilityMissing):
        p._raise_for(2, {"error": "gi missing"}, "")


def test_exit_code_pipewire_fd_is_transient_not_missing():
    """A broken fd is a bad attempt, not an absent capability."""
    p = PortalCapture()
    with pytest.raises(TransientError):
        p._raise_for(8, {"error": "OpenPipeWireRemote failed"}, "")


def test_missing_helper_is_capability_missing():
    p = PortalCapture(helper=Path("/nonexistent/helper.py"))
    with pytest.raises(CapabilityMissing):
        p.probe()


def test_capture_rejects_helper_ok_without_file(tmp_path, monkeypatch):
    """Provider must never return a Frame for a file that isn't there."""
    p = PortalCapture()
    monkeypatch.setattr(p, "_preflight", lambda: None)
    monkeypatch.setattr(
        p,
        "_run",
        lambda args, timeout_s: (
            0,
            {"ok": True, "path": str(tmp_path / "ghost.png"), "streams": []},
            "",
        ),
    )
    with pytest.raises(TransientError, match="no file"):
        p.capture(tmp_path / "ghost.png")


def test_png_size_reads_ihdr(tmp_path):
    from hermes_computer_bridge.portal_capture import _png_size

    f = tmp_path / "a.png"
    f.write_bytes(PNG_1x1)
    assert _png_size(f) == (1, 1)


def test_png_size_rejects_non_png(tmp_path):
    from hermes_computer_bridge.portal_capture import _png_size

    f = tmp_path / "b.png"
    f.write_bytes(b"not a png at all........")
    with pytest.raises(TransientError):
        _png_size(f)


def test_frame_to_dict_carries_all_streams():
    s0, s1 = _stream(52, (2048, 1152)), _stream(53, (1920, 1080), index=1)
    frame = Frame(
        path=Path("/tmp/x.png"),
        width=2048,
        height=1152,
        bytes_len=10,
        stream=s0,
        rung="portal-screencast",
        all_streams=[s0, s1],
    )
    d = frame.to_dict()
    assert d["stream_count"] == 2
    assert [s["node_id"] for s in d["streams"]] == [52, 53]
    assert json.dumps(d)  # must be serialisable for the REST layer
