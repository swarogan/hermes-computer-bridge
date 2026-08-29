#!/usr/bin/python3
"""Does the predicted dead band match actual black pixels in the frame?

geometry.py predicts: HDMI-A-1 sits at y=+72, so frame rows 0..71 for
x>=2048 belong to no output. If the model is right, those pixels are black
AND frame_to_logical() returns None there.
"""
import sys
from pathlib import Path

ROOT = Path("/run/media/Workspace/Projekty/hermes-computer-bridge")
sys.path.insert(0, str(ROOT))

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from hermes_computer_bridge.capture_service import read_outputs  # noqa: E402
from hermes_computer_bridge.geometry import frame_to_logical  # noqa: E402

png = ROOT / "evidence" / "abc-frame-unlocked.png"
pb = GdkPixbuf.Pixbuf.new_from_file(str(png))
data, n, rs = pb.get_pixels(), pb.get_n_channels(), pb.get_rowstride()
STREAM = (pb.get_width(), pb.get_height())
outputs = read_outputs()
print("stream", STREAM)
print("outputs", [o.to_dict() for o in outputs])


def px(x, y):
    off = y * rs + x * n
    return data[off], data[off + 1], data[off + 2]


def mean_lum(points):
    vals = []
    for x, y in points:
        r, g, b = px(x, y)
        vals.append(0.299 * r + 0.587 * g + 0.114 * b)
    return sum(vals) / len(vals)


# Predicted DEAD: x >= 2048, y < 72
dead = [(x, y) for x in range(2100, 3900, 200) for y in range(2, 70, 10)]
# Predicted LIVE on HDMI: x >= 2048, y > 72
live_hdmi = [(x, y) for x in range(2100, 3900, 200) for y in range(90, 900, 100)]
# Predicted LIVE on DP-1
live_dp = [(x, y) for x in range(50, 2000, 200) for y in range(10, 1100, 100)]

print(f"\npredicted DEAD  ({len(dead)} px): mean_luma = {mean_lum(dead):.2f}")
print(f"predicted LIVE HDMI ({len(live_hdmi)} px): mean_luma = {mean_lum(live_hdmi):.2f}")
print(f"predicted LIVE DP-1 ({len(live_dp)} px): mean_luma = {mean_lum(live_dp):.2f}")

dead_black = all(px(x, y) == (0, 0, 0) for x, y in dead)
print(f"\nevery predicted-dead pixel is pure black: {dead_black}")

# And the model must refuse to map them.
mapped_dead = [
    frame_to_logical(x, y, stream_size=STREAM, stream_position=None, outputs=outputs)
    for x, y in dead
]
print(f"frame_to_logical returns None for all dead px: {all(m is None for m in mapped_dead)}")

mapped_live = [
    frame_to_logical(x, y, stream_size=STREAM, stream_position=None, outputs=outputs)
    for x, y in live_hdmi
]
print(f"frame_to_logical maps all live HDMI px: {all(m is not None for m in mapped_live)}")
if mapped_live[0]:
    print("  sample:", mapped_live[0].to_dict())

ok = dead_black and all(m is None for m in mapped_dead) and all(m is not None for m in mapped_live)
print("\nVERDICT:", "GEOMETRY MODEL MATCHES REALITY" if ok else "MISMATCH")
raise SystemExit(0 if ok else 1)
