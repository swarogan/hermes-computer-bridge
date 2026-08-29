"""/status must carry what the pane actually renders."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = (ROOT / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
JS = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
TRANSPORT = (ROOT / "hermes_computer_bridge" / "frame_transport.py").read_text(encoding="utf-8")
SERVED = API + TRANSPORT


def test_status_supplies_every_field_the_pane_reads():
    """A field the pane reads but /status never sets renders as undefined.

    frame_version/frame_name/frame_bytes are produced by frame_summary(), so
    the contract is checked where those keys actually live.
    """
    read_by_pane = [
        "frame_version",
        "frame_blank",
    ]
    for field in read_by_pane:
        assert f"status.data?.{field}" in JS or f"status.data.{field}" in JS, field
        assert f'"{field}"' in SERVED, f"/status never sets {field}"

    # The monitor picker crops from the output list.
    assert "status.data?.outputs" in JS
    assert '"outputs"' in API


def test_stream_cache_is_labelled_as_a_cache_not_live_truth():
    assert '"streams_are_cached"' in API
    assert "_last_capture" in API


def test_capture_records_every_stream_not_just_the_selected_one():
    assert "frame.all_streams" in API
    assert '_last_capture["streams"]' in API


def test_monitor_crop_uses_geometry_not_a_second_portal_session():
    """KDE returns ONE bounding-box stream; per-monitor view is a client crop."""
    # The canvas draws a sub-rectangle of the frame image...
    assert "function FrameCanvas({ dataUrl, region" in JS
    assert "drawImage(image, sx, sy, sw, sh," in JS
    # ...using the output's verified geometry (x/y/width/height).
    assert "selected.width" in JS
    assert "selected.height" in JS
    assert "selected.x" in JS
    assert "selected.y" in JS
    # No per-monitor portal session was added: capture still takes no
    # monitor argument, and the picker lists outputs, not streams.
    assert "stream_index: streamIndex" not in JS
    assert "OutputPicker" in JS
    assert "StreamPicker" not in JS


def test_pane_only_offers_a_picker_when_several_outputs_exist():
    # A single-monitor machine has nothing to pick.
    assert "outputs.length < 2" in JS


def test_first_enabled_output_is_selected_by_default():
    assert "useState(undefined)" in JS
    assert "outputName === undefined && outputs.length > 0" in JS
    assert "setOutputName(outputs[0].name)" in JS
    # The "All screens" aggregate option was dropped: only real monitors list.
    assert "All screens" not in JS
