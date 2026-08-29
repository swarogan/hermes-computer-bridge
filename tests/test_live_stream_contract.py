"""Behavioral contract for the long-lived portal live stream."""

from __future__ import annotations

import base64
import importlib.util
import os
import subprocess
from pathlib import Path

from hermes_computer_bridge import frame_transport

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "helpers" / "portal_screencast.py"

_spec = importlib.util.spec_from_file_location("portal_screencast", HELPER)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


def test_frame_transport_accepts_live_jpeg(tmp_path):
    frame = tmp_path / "live-frame.jpg"
    raw = b"\xff\xd8\xff\xe0" + b"live-jpeg" + b"\xff\xd9"
    frame.write_bytes(raw)

    payload = frame_transport.frame_data(frame)

    assert payload["media_type"] == "image/jpeg"
    assert payload["data_url"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(payload["data_url"].split(",", 1)[1]) == raw


def test_latest_frame_includes_live_jpeg(tmp_path):
    frame = tmp_path / "live-frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0x\xff\xd9")

    assert frame_transport.latest_frame(tmp_path) == frame


def _helper_help(*args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/python3", str(HELPER), *args, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_helper_exposes_long_lived_stream_command():
    assert "stream" in _helper_help()


def test_stream_command_takes_the_live_pipeline_flags():
    """A one-shot `spike` cannot express fps/quality — the stream must."""
    out = _helper_help("stream")
    for flag in (
        "--output",
        "--fps",
        "--quality",
        "--stream-index",
        "--node-id",
        "--persist-mode",
        "--timeout",
    ):
        assert flag in out, flag


def test_probe_and_spike_survive_the_new_subcommand():
    assert "--stream-index" in _helper_help("spike")
    assert "probe" in _helper_help()


# --- atomic frame swap -------------------------------------------------
#
# The REST reader opens the live file at an arbitrary moment. A naive
# `path.write_bytes(data)` truncates in place, so a reader that opened the
# file first sees a TRUNCATED image. os.replace() swaps a new inode in, so
# an already-open descriptor keeps yielding the previous COMPLETE frame.


def test_frame_swap_never_truncates_a_frame_a_reader_already_opened(tmp_path):
    live = tmp_path / "live-frame.jpg"
    old = b"\xff\xd8\xff\xe0" + b"O" * 4096 + b"\xff\xd9"
    new = b"\xff\xd8\xff\xe0" + b"N" * 64 + b"\xff\xd9"
    helper.write_frame_atomically(live, old)

    with live.open("rb") as reader_fd:
        helper.write_frame_atomically(live, new)
        still_readable = reader_fd.read()

    assert still_readable == old, "in-place truncation clobbered an open reader"
    assert live.read_bytes() == new


def test_frame_swap_leaves_no_temp_files_behind(tmp_path):
    live = tmp_path / "live-frame.jpg"
    helper.write_frame_atomically(live, b"\xff\xd8\xff\xe0a\xff\xd9")
    helper.write_frame_atomically(live, b"\xff\xd8\xff\xe0bb\xff\xd9")

    assert [p.name for p in tmp_path.iterdir()] == ["live-frame.jpg"]


def test_only_complete_jpeg_buffers_are_publishable():
    assert helper.is_complete_jpeg(b"\xff\xd8\xffpayload\xff\xd9") is True
    assert helper.is_complete_jpeg(b"\xff\xd8\xffpayload") is False
    assert helper.is_complete_jpeg(b"") is False


# --- clamping ----------------------------------------------------------


def test_fps_is_clamped_to_a_sane_range():
    assert helper.clamp_fps(0) == helper.MIN_FPS
    assert helper.clamp_fps(-5) == helper.MIN_FPS
    assert helper.clamp_fps(9999) == helper.MAX_FPS
    assert helper.clamp_fps(10) == 10


def test_quality_is_clamped_to_a_sane_range():
    assert helper.clamp_quality(0) == helper.MIN_QUALITY
    assert helper.clamp_quality(500) == helper.MAX_QUALITY
    assert helper.clamp_quality(75) == 75


# --- blank frames are never healthy ------------------------------------


def test_blank_frame_is_reported_but_never_healthy():
    payload = helper.stream_status(
        event="status", frames=42, stats={"available": True, "blank": True}
    )

    assert payload["blank"] is True
    assert payload["healthy"] is False
    assert payload["frames"] == 42


def test_a_real_frame_is_healthy():
    payload = helper.stream_status(
        event="live", frames=1, stats={"available": True, "blank": False}
    )

    assert payload["healthy"] is True
    assert payload["blank"] is False
    assert payload["event"] == "live"


def test_zero_frames_is_never_healthy_whatever_the_stats_say():
    payload = helper.stream_status(event="status", frames=0, stats={"blank": False})

    assert payload["healthy"] is False


# --- helper `stream()` implementation contract -------------------------
#
# The real handshake needs a portal + a consent dialog + a compositor, so it
# cannot run in a headless test. These pin the invariants that would silently
# rot: one session, one fd, no streams[0] hardcode, warm-up skip kept, clean
# teardown. They are a guard rail, NOT proof the stream works.

import inspect

HELPER_SRC = HELPER.read_text(encoding="utf-8")
STREAM_SRC = inspect.getsource(helper.stream_forever)
HANDSHAKE_SRC = inspect.getsource(helper.portal_handshake)


def test_stream_selects_a_monitor_instead_of_hardcoding_the_first():
    assert "select_stream(" in HANDSHAKE_SRC
    assert "streams[0]" not in HANDSHAKE_SRC
    assert "streams[0]" not in STREAM_SRC


def test_stream_does_the_portal_handshake_exactly_once():
    """One session for the process lifetime, not one per frame."""
    # CreateSession, SelectDevices (input), SelectSources, Start.
    assert HANDSHAKE_SRC.count("client.call_request(") == 4
    assert HANDSHAKE_SRC.count("open_pipewire_remote(") == 1
    # The streaming function delegates; it must not re-enter the handshake.
    assert STREAM_SRC.count("portal_handshake(") == 1
    assert "call_request(" not in STREAM_SRC
    for method in ("CreateSession", "SelectSources"):
        assert method not in STREAM_SRC, f"{method} must not be re-run per stream loop"


def test_spike_and_stream_share_one_handshake_so_they_cannot_drift():
    assert "portal_handshake(" in inspect.getsource(helper.spike)


def test_stream_keeps_consent_at_the_maximum_allowed_reduction():
    assert "persist_mode" in HANDSHAKE_SRC
    assert "restore_token" in HANDSHAKE_SRC
    # Never fabricate consent.
    assert "ydotool" not in HELPER_SRC
    assert "xdotool" not in HELPER_SRC


def test_stream_pipeline_is_pipewire_to_jpeg_not_an_external_grabber():
    """Portal ScreenCast stays the capture rung — no shelling out to a
    desktop grabber. (The graph itself is built for real in the
    `build_live_pipeline` tests below.)"""
    builder = inspect.getsource(helper.build_live_pipeline)
    assert "pipewiresrc" in builder
    assert "jpegenc" in builder
    assert "videorate" in builder
    assert "build_live_pipeline(" in STREAM_SRC
    for banned in ("x11vnc", "wayvnc", "websockify", "ffmpeg", "novnc"):
        assert banned not in HELPER_SRC.lower(), banned


def test_stream_keeps_the_warmup_skip_because_first_buffers_are_black():
    assert "skip" in STREAM_SRC.lower()
    assert "frame_stats" in STREAM_SRC
    assert "write_frame_atomically" in STREAM_SRC


def test_stream_announces_readiness_and_keeps_reporting():
    assert '"live"' in STREAM_SRC, "parent must not have to sleep-and-hope"
    assert "stream_status(" in STREAM_SRC
    assert "selected_stream" in STREAM_SRC
    assert '"streams"' in STREAM_SRC
    assert '"output"' in STREAM_SRC
    assert '"status"' in STREAM_SRC, "a stalled stream must be distinguishable"


def test_stream_tears_the_session_down_on_a_signal():
    assert "SIGTERM" in STREAM_SRC
    assert "SIGINT" in STREAM_SRC
    assert "unix_signal_add" in STREAM_SRC, "a signal must reach the GLib loop"
    # Teardown always runs, not only on the happy path.
    assert "finally:" in STREAM_SRC
    assert "session.close()" in STREAM_SRC
    close_src = inspect.getsource(helper.PortalSession.close)
    assert "os.close" in close_src, "the PipeWire fd must be released"
    assert "close_session" in close_src, "the portal session must be closed"


def test_session_close_is_idempotent_so_signal_and_finally_cannot_double_free():
    calls = []

    class FakeClient:
        def close_session(self, path):
            calls.append(path)

    session = helper.PortalSession(
        client=FakeClient(),
        session_path="/session/1",
        parsed=[],
        chosen={},
        fd=None,
        restore_saved=False,
        log=[],
    )
    session.close()
    session.close()

    assert calls == ["/session/1"]


def test_the_atomic_temp_file_is_never_mistaken_for_a_frame(tmp_path):
    """Atomicity only helps if the reader also ignores the in-flight file.

    latest_frame() picks by mtime, so a temp named `live-frame-new.jpg`
    would win the race and be served half-written.
    """
    live = tmp_path / "live-frame.jpg"
    helper.write_frame_atomically(live, b"\xff\xd8\xff\xe0done\xff\xd9")
    # Recreate what a kill mid-write leaves behind, and make it NEWER than
    # the good frame — otherwise mtime ordering would hide the bug.
    partial = live.with_name(f".{live.name}.part")
    partial.write_bytes(b"\xff\xd8\xff\xe0half")
    newer = live.stat().st_mtime_ns + 1_000_000_000
    os.utime(partial, ns=(newer, newer))
    assert partial.stat().st_mtime_ns > live.stat().st_mtime_ns

    assert frame_transport.latest_frame(tmp_path) == live


# --- the GStreamer graph, verified for real --------------------------------
#
# The portal handshake cannot run headless, but the pipeline GRAPH can be
# built without one. That is where the silent breakage lives: a mistyped caps
# string, a property that does not exist on this GStreamer, or a pad pair that
# will not link. Catching those here beats catching them on the user's desktop.

import pytest


def _gst():
    gi = pytest.importorskip("gi")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    return Gst


def test_the_live_pipeline_links_end_to_end(tmp_path):
    Gst = _gst()
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        pipeline, elements = helper.build_live_pipeline(
            Gst, pw_fd=fd, node_id=52, fps=10, quality=70, on_sample=lambda _s: None
        )
    finally:
        os.close(fd)

    # Every element found a downstream peer: the chain really is connected.
    for name in ("src", "conv", "rate", "caps", "enc"):
        pad = elements[name].get_static_pad("src")
        assert pad is not None and pad.get_peer() is not None, f"{name} is dangling"
    pipeline.set_state(Gst.State.NULL)


def test_the_framerate_cap_is_a_caps_string_gstreamer_actually_parses():
    """A typo here silently yields NULL caps and an unpaced firehose."""
    Gst = _gst()
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        pipeline, elements = helper.build_live_pipeline(
            Gst, pw_fd=fd, node_id=52, fps=12, quality=70, on_sample=lambda _s: None
        )
    finally:
        os.close(fd)

    caps = elements["caps"].get_property("caps")
    assert caps is not None
    assert "framerate=(fraction)12/1" in caps.to_string()
    pipeline.set_state(Gst.State.NULL)


def test_the_sink_drops_stale_frames_instead_of_queueing_a_backlog():
    """A live view wants the NEWEST frame; a queue turns lag into a growing
    delay that never recovers."""
    Gst = _gst()
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        pipeline, elements = helper.build_live_pipeline(
            Gst, pw_fd=fd, node_id=52, fps=10, quality=64, on_sample=lambda _s: None
        )
    finally:
        os.close(fd)

    assert elements["sink"].get_property("drop") is True
    assert elements["sink"].get_property("max-buffers") == 1
    assert elements["enc"].get_property("quality") == 64
    pipeline.set_state(Gst.State.NULL)
