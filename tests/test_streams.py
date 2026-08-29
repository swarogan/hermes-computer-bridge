"""Stream metadata must survive multi-monitor sessions.

These import the helper by path because it targets /usr/bin/python3 and
lives outside the package; parse_streams/select_stream are pure and run
anywhere without gi.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "helpers" / "portal_screencast.py"

spec = importlib.util.spec_from_file_location("portal_screencast", HELPER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Shape as returned by Start: a(ua{sv})
TWO_MONITORS = [
    (52, {"size": (2560, 1440), "position": (0, 0), "source_type": 1, "id": "DP-1"}),
    (53, {"size": (1408, 1152), "position": (2560, 0), "source_type": 1, "id": "HDMI-A-1"}),
]


def test_parse_keeps_every_stream():
    parsed = mod.parse_streams(TWO_MONITORS)
    assert len(parsed) == 2
    assert [s["node_id"] for s in parsed] == [52, 53]


def test_parse_keeps_size_and_position():
    parsed = mod.parse_streams(TWO_MONITORS)
    assert parsed[0]["size"] == [2560, 1440]
    assert parsed[0]["position"] == [0, 0]
    # Second monitor is offset — input mapping depends on this.
    assert parsed[1]["position"] == [2560, 0]
    assert parsed[1]["size"] == [1408, 1152]


def test_parse_keeps_identifiers():
    parsed = mod.parse_streams(TWO_MONITORS)
    assert parsed[0]["id"] == "DP-1"
    assert parsed[1]["id"] == "HDMI-A-1"
    assert parsed[0]["source_type"] == 1


def test_select_defaults_to_first_but_records_all():
    parsed = mod.parse_streams(TWO_MONITORS)
    chosen = mod.select_stream(parsed)
    assert chosen["node_id"] == 52
    assert chosen["index"] == 0


def test_select_by_index():
    parsed = mod.parse_streams(TWO_MONITORS)
    assert mod.select_stream(parsed, stream_index=1)["node_id"] == 53


def test_select_by_node_id_overrides_index():
    parsed = mod.parse_streams(TWO_MONITORS)
    chosen = mod.select_stream(parsed, stream_index=0, node_id=53)
    assert chosen["node_id"] == 53


def test_select_rejects_unknown_node_id():
    parsed = mod.parse_streams(TWO_MONITORS)
    with pytest.raises(ValueError, match="not among streams"):
        mod.select_stream(parsed, node_id=999)


def test_select_rejects_out_of_range_index():
    parsed = mod.parse_streams(TWO_MONITORS)
    with pytest.raises(ValueError, match="out of range"):
        mod.select_stream(parsed, stream_index=5)


def test_parse_rejects_bad_shape():
    with pytest.raises(ValueError, match="unexpected stream shape"):
        mod.parse_streams([42])


def test_parse_tolerates_missing_props():
    parsed = mod.parse_streams([(7, {})])
    assert parsed[0]["node_id"] == 7
    assert parsed[0]["size"] is None
    assert parsed[0]["position"] is None
