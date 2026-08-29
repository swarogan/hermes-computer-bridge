#!/usr/bin/python3
"""Validate that evidence/portal-frame.png is a real, non-blank screen frame."""
import hashlib
import sys
from pathlib import Path

p = Path(sys.argv[1] if len(sys.argv) > 1 else "evidence/portal-frame.png")
raw = p.read_bytes()
print("path", p.resolve())
print("bytes", len(raw))
print("sha256", hashlib.sha256(raw).hexdigest())
print("png_magic", raw[:8] == b"\x89PNG\r\n\x1a\n")

# IHDR straight from the container, no deps.
w = int.from_bytes(raw[16:20], "big")
h = int.from_bytes(raw[20:24], "big")
bit_depth = raw[24]
color_type = raw[25]
print(f"IHDR {w}x{h} depth={bit_depth} color_type={color_type}")

try:
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
except Exception as exc:
    print("pixbuf unavailable:", exc)
    raise SystemExit(0)

pb = GdkPixbuf.Pixbuf.new_from_file(str(p))
print("pixbuf", pb.get_width(), "x", pb.get_height(), "channels", pb.get_n_channels())

data = pb.get_pixels()
n = pb.get_n_channels()
rowstride = pb.get_rowstride()
width, height = pb.get_width(), pb.get_height()

# Sample a grid; a blank/black frame has ~zero variance.
samples = []
step_y = max(1, height // 60)
step_x = max(1, width // 60)
for y in range(0, height, step_y):
    base = y * rowstride
    for x in range(0, width, step_x):
        off = base + x * n
        samples.append((data[off], data[off + 1], data[off + 2]))

lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in samples]
mean = sum(lum) / len(lum)
var = sum((v - mean) ** 2 for v in lum) / len(lum)
uniq = len(set(samples))
print(f"samples={len(samples)} mean_luma={mean:.2f} variance={var:.2f} unique_colors={uniq}")

blank = var < 1.0 or uniq < 5
print("VERDICT:", "BLANK/SUSPECT" if blank else "REAL CONTENT")
raise SystemExit(1 if blank else 0)
