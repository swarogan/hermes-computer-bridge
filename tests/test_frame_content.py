"""Frame-content invariants, proven against a real unlocked-session capture.

These read the committed evidence PNG when it is present. They are skipped
(not silently passed) when it is absent, so a fresh clone does not claim
verification it did not do.
"""

from pathlib import Path

import pytest

from hermes_computer_bridge.geometry import Output, frame_to_logical

ROOT = Path(__file__).resolve().parent.parent
FRAME = ROOT / "evidence" / "abc-frame-unlocked.png"

# Measured layout of the machine this evidence came from.
OUTPUTS = [
    Output("DP-1", 0, 0, 2048, 1152),
    Output("HDMI-A-1", 2048, 72, 1920, 1080),
]
STREAM = (3968, 1152)

pytestmark = pytest.mark.skipif(
    not FRAME.is_file(), reason="evidence frame not present (gitignored PNG)"
)


def _pixbuf():
    gi = pytest.importorskip("gi")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    return GdkPixbuf.Pixbuf.new_from_file(str(FRAME))


def _sampler(pb):
    data, n, rs = pb.get_pixels(), pb.get_n_channels(), pb.get_rowstride()

    def px(x, y):
        off = y * rs + x * n
        return data[off], data[off + 1], data[off + 2]

    return px


def test_evidence_frame_matches_expected_stream_size():
    pb = _pixbuf()
    assert (pb.get_width(), pb.get_height()) == STREAM


def test_evidence_frame_is_not_blank():
    """The locked-screen run produced variance 0; this one must not."""
    pb = _pixbuf()
    px = _sampler(pb)
    lum = []
    for y in range(100, 1000, 100):
        for x in range(100, 1900, 100):
            r, g, b = px(x, y)
            lum.append(0.299 * r + 0.587 * g + 0.114 * b)
    mean = sum(lum) / len(lum)
    var = sum((v - mean) ** 2 for v in lum) / len(lum)
    assert var > 1.0, "frame looks blank"


def test_predicted_dead_band_is_actually_black():
    """geometry.py says rows <72 right of x=2048 belong to no output.

    If the model were wrong, these pixels would contain desktop content.
    """
    px = _sampler(_pixbuf())
    for x in range(2100, 3900, 200):
        for y in range(2, 70, 10):
            assert px(x, y) == (0, 0, 0), f"expected dead band at {x},{y}"


def test_live_region_right_of_offset_has_content():
    px = _sampler(_pixbuf())
    lum = []
    for x in range(2100, 3900, 200):
        for y in range(90, 900, 100):
            r, g, b = px(x, y)
            lum.append(0.299 * r + 0.587 * g + 0.114 * b)
    assert sum(lum) / len(lum) > 10.0


def test_model_refuses_to_map_the_dead_band():
    for x in range(2100, 3900, 400):
        for y in range(2, 70, 20):
            assert (
                frame_to_logical(
                    x, y, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
                )
                is None
            )


def test_model_maps_live_pixels_to_the_right_output():
    p = frame_to_logical(
        2100, 90, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
    )
    assert p is not None and p.output == "HDMI-A-1"
    q = frame_to_logical(
        100, 100, stream_size=STREAM, stream_position=None, outputs=OUTPUTS
    )
    assert q is not None and q.output == "DP-1"
