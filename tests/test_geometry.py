"""Geometry from the machine's real dual-head layout.

DP-1      0,0     2048x1152
HDMI-A-1  2048,72 1920x1080
portal:   ONE stream 3968x1152, position=None
"""

import pytest

from hermes_computer_bridge.geometry import (
    MODE_BOUNDING_BOX,
    MODE_SINGLE_OUTPUT,
    MODE_STREAM_POSITION,
    MODE_UNKNOWN,
    Output,
    bounding_box,
    dead_band_rects,
    frame_to_logical,
    parse_kscreen_outputs,
    resolve_origin,
)

KSCREEN = """Output: 1 DP-1 6e29c9d4-d1c6-48ce-8dbb-421b86e25f46
\tenabled
\tGeometry: 0,0 2048x1152
Output: 2 HDMI-A-1 591b9868-1476-47ce-81fb-8f811bab0e16
\tenabled
\tGeometry: 2048,72 1920x1080
"""

OUTPUTS = [
    Output("DP-1", 0, 0, 2048, 1152),
    Output("HDMI-A-1", 2048, 72, 1920, 1080),
]
STREAM = (3968, 1152)


def test_parse_real_kscreen_output():
    outs = parse_kscreen_outputs(KSCREEN)
    assert [o.name for o in outs] == ["DP-1", "HDMI-A-1"]
    assert (outs[1].x, outs[1].y) == (2048, 72)
    assert (outs[1].width, outs[1].height) == (1920, 1080)


def test_parse_skips_disabled():
    text = KSCREEN + "Output: 3 DP-2 x\n\tdisabled\n\tGeometry: 0,0 800x600\n"
    assert [o.name for o in parse_kscreen_outputs(text)] == ["DP-1", "HDMI-A-1"]


def test_bounding_box_matches_portal_stream_size():
    bbox = bounding_box(OUTPUTS)
    assert bbox == (0, 0, 3968, 1152)
    # This is exactly what the portal handed back as a single stream.
    assert (bbox[2], bbox[3]) == STREAM


def test_origin_bounding_box_when_position_absent():
    origin, mode = resolve_origin(STREAM, None, OUTPUTS)
    assert origin == (0, 0)
    assert mode == MODE_BOUNDING_BOX


def test_origin_prefers_backend_reported_position():
    origin, mode = resolve_origin((1920, 1080), (2048, 72), OUTPUTS)
    assert origin == (2048, 72)
    assert mode == MODE_STREAM_POSITION


def test_origin_single_output_match():
    origin, mode = resolve_origin((1920, 1080), None, OUTPUTS)
    assert origin == (2048, 72)
    assert mode == MODE_SINGLE_OUTPUT


def test_origin_refuses_to_guess_on_ambiguous_size():
    twins = [Output("A", 0, 0, 1920, 1080), Output("B", 1920, 0, 1920, 1080)]
    origin, mode = resolve_origin((1920, 1080), None, twins)
    assert origin is None
    assert mode == MODE_UNKNOWN


def test_point_on_primary_maps_directly():
    p = frame_to_logical(
        100, 100, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
    )
    assert p is not None
    assert (p.x, p.y, p.output) == (100, 100, "DP-1")


def test_point_on_secondary_keeps_offset():
    # 100px into HDMI horizontally, below its 72px top offset.
    p = frame_to_logical(
        2148, 200, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
    )
    assert p is not None
    assert p.output == "HDMI-A-1"
    assert (p.x, p.y) == (2148, 200)


def test_dead_band_returns_none_not_zero():
    """Top-right strip: HDMI starts 72px down, so y<72 there is nothing."""
    p = frame_to_logical(
        3000, 10, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
    )
    assert p is None


def test_just_below_dead_band_is_live():
    assert (
        frame_to_logical(
            3000, 80, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
        )
        is not None
    )


def test_bottom_right_of_hdmi_is_dead():
    # HDMI is 1080 tall starting at 72 -> ends at 1152; frame is 1152 tall.
    assert (
        frame_to_logical(
            3000, 1151, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
        )
        is not None
    )


def test_out_of_frame_rejected():
    assert (
        frame_to_logical(
            5000, 10, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
        )
        is None
    )
    assert (
        frame_to_logical(
            -1, 10, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
        )
        is None
    )


def test_downscaled_preview_maps_back():
    # A half-size preview click must land at full-size coordinates.
    p = frame_to_logical(
        50,
        50,
        stream_size=STREAM,
        stream_position=None,
        outputs=OUTPUTS,
        frame_size=(1984, 576),
    )
    assert p is not None
    assert (p.x, p.y) == (100, 100)


def test_no_outputs_means_no_guess():
    assert (
        frame_to_logical(10, 10, stream_size=STREAM, stream_position=None, outputs=[])
        is None
    )


def test_dead_band_report_flags_the_gap():
    report = dead_band_rects(STREAM, None, OUTPUTS)
    assert report
    entry = report[0]
    assert entry["mode"] == MODE_BOUNDING_BOX
    assert entry["has_dead_band"] is True
    assert 0.9 < entry["covered_fraction"] < 1.0
    names = {r["output"] for r in entry["output_rects"]}
    assert names == {"DP-1", "HDMI-A-1"}
