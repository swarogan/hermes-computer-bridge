"""Dashboard transport contract — no graphical session required."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from hermes_computer_bridge import frame_transport

ROOT = Path(__file__).resolve().parent.parent
API_PATH = ROOT / "dashboard" / "plugin_api.py"


def test_ctx_rest_frame_transport_is_json_not_file_response():
    source = API_PATH.read_text(encoding="utf-8")
    assert "from fastapi.responses import FileResponse" not in source
    assert '@router.get("/frame-data")' in source
    assert "encode_frame_data" in source
    assert '"data_url"' in (ROOT / "hermes_computer_bridge" / "frame_transport.py").read_text()


def test_frame_data_is_png_data_url(tmp_path):
    source = ROOT / "evidence" / "abc-frame-unlocked.png"
    assert source.exists(), "step-2 evidence is part of this repository"
    frame = tmp_path / "frame.png"
    frame.write_bytes(source.read_bytes())

    payload = frame_transport.frame_data(frame)
    assert payload["frame_present"] is True
    assert payload["frame_version"] == frame_transport.frame_version(frame)
    assert payload["media_type"] == "image/png"
    assert payload["data_url"].startswith("data:image/png;base64,")
    decoded = base64.b64decode(payload["data_url"].split(",", 1)[1])
    assert decoded == frame.read_bytes()


def test_frame_version_changes_with_file(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\nA")
    before = frame_transport.frame_version(frame)
    frame.write_bytes(b"\x89PNG\r\n\x1a\nAB")
    after = frame_transport.frame_version(frame)
    assert before != after


def test_latest_frame_is_selected_by_mtime(tmp_path):
    older = tmp_path / "older.png"
    newer = tmp_path / "newer.png"
    older.write_bytes(b"\x89PNG\r\n\x1a\nold")
    newer.write_bytes(b"\x89PNG\r\n\x1a\nnew")
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    assert frame_transport.latest_frame(tmp_path) == newer


def test_invalid_png_is_rejected(tmp_path):
    frame = tmp_path / "bad.png"
    frame.write_bytes(b"not a png")
    with pytest.raises(frame_transport.InvalidFrame, match="not a PNG"):
        frame_transport.frame_data(frame)


def test_events_route_is_socket_accelerator_not_poll_replacement():
    source = API_PATH.read_text(encoding="utf-8")
    js = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    assert '@router.websocket("/events")' in source
    assert "ctx.socket('/events'" in js
    assert f"refetchInterval: POLL_MS" in js
    assert "const POLL_MS = 2000" in js
