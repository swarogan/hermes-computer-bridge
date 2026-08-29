"""Frame pixels -> logical desktop coordinates.

Measured reality on KDE Wayland (2026-08-26), not an assumption:

    kscreen-doctor: DP-1      0,0     2048x1152
                    HDMI-A-1  2048,72 1920x1080
    portal Start:   ONE stream, size 3968x1152, position=None

3968 = 2048+1920 and 1152 = max(1152, 72+1080), i.e. the portal handed back
the BOUNDING BOX of both outputs as a single stream and did not say where it
starts. Two traps follow:

1. A provider that defaults `position` to (0, 0) is guessing. We keep None.
2. HDMI-A-1 sits 72px lower, so the frame has a DEAD BAND — the top-right
   rectangle belongs to no output. Naive frame->screen mapping lands on a
   pixel that does not exist.

This module is pure arithmetic: no D-Bus, no input injection. It exists so
that when the input rung is built it has a correct model to stand on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class Output:
    """A physical output in logical (compositor) coordinates."""

    name: str
    x: int
    y: int
    width: int
    height: int
    enabled: bool = True

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, gx: int, gy: int) -> bool:
        return self.x <= gx < self.right and self.y <= gy < self.bottom

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class LogicalPoint:
    x: int
    y: int
    output: str

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "output": self.output}


# Origin resolution modes — how we decided where the frame starts.
MODE_STREAM_POSITION = "stream-position"  # backend told us (GNOME/wlroots do)
MODE_BOUNDING_BOX = "bounding-box"  # frame covers every output (KDE case)
MODE_SINGLE_OUTPUT = "single-output"  # frame matches exactly one output
MODE_UNKNOWN = "unknown"  # refuse to guess


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_GEOMETRY_RE = re.compile(
    r"^\s*Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)", re.MULTILINE
)
_OUTPUT_RE = re.compile(r"^Output:\s*\d+\s+(\S+)", re.MULTILINE)


def strip_ansi(text: str) -> str:
    """kscreen-doctor colourises even when piped — strip before parsing."""
    return _ANSI_RE.sub("", text)


def parse_kscreen_outputs(text: str) -> list[Output]:
    """Parse `kscreen-doctor -o` output.

    KDE-specific *source of truth for positions*, NOT a KDE branch in the
    capture path: the portal rung stays the same everywhere, this only fills
    in geometry the portal refused to provide. Other compositors that DO
    populate stream position never reach this function.
    """
    text = strip_ansi(text)
    outputs: list[Output] = []
    blocks = re.split(r"(?=^Output:)", text, flags=re.MULTILINE)
    for block in blocks:
        if not block.strip().startswith("Output:"):
            continue
        name_m = _OUTPUT_RE.search(block)
        geo_m = _GEOMETRY_RE.search(block)
        if not name_m or not geo_m:
            continue
        enabled = "disabled" not in block.split("Geometry:")[0]
        outputs.append(
            Output(
                name=name_m.group(1),
                x=int(geo_m.group(1)),
                y=int(geo_m.group(2)),
                width=int(geo_m.group(3)),
                height=int(geo_m.group(4)),
                enabled=enabled,
            )
        )
    return [o for o in outputs if o.enabled]


def bounding_box(outputs: Sequence[Output]) -> Optional[tuple[int, int, int, int]]:
    """(x, y, width, height) covering every enabled output."""
    live = [o for o in outputs if o.enabled]
    if not live:
        return None
    x0 = min(o.x for o in live)
    y0 = min(o.y for o in live)
    x1 = max(o.right for o in live)
    y1 = max(o.bottom for o in live)
    return (x0, y0, x1 - x0, y1 - y0)


def resolve_origin(
    stream_size: Optional[tuple[int, int]],
    stream_position: Optional[tuple[int, int]],
    outputs: Sequence[Output],
) -> tuple[Optional[tuple[int, int]], str]:
    """Where does this frame's (0, 0) sit in logical coordinates?

    Returns (origin, mode). origin is None when we refuse to guess.
    """
    if stream_position is not None:
        return stream_position, MODE_STREAM_POSITION
    if stream_size is None:
        return None, MODE_UNKNOWN

    bbox = bounding_box(outputs)
    if bbox and (bbox[2], bbox[3]) == stream_size:
        return (bbox[0], bbox[1]), MODE_BOUNDING_BOX

    matches = [o for o in outputs if (o.width, o.height) == stream_size]
    if len(matches) == 1:
        return (matches[0].x, matches[0].y), MODE_SINGLE_OUTPUT

    # Several outputs share this size, or nothing matches. Guessing here is
    # exactly the bug class this project exists to avoid.
    return None, MODE_UNKNOWN


def frame_to_logical(
    fx: int,
    fy: int,
    *,
    stream_size: Optional[tuple[int, int]],
    stream_position: Optional[tuple[int, int]],
    outputs: Sequence[Output],
    frame_size: Optional[tuple[int, int]] = None,
) -> Optional[LogicalPoint]:
    """Map a frame pixel to a logical desktop point.

    Returns None when the point falls in a dead band (no output covers it)
    or when the origin could not be resolved. None means "do not click",
    never "click at 0,0".
    """
    if stream_size is None:
        return None
    origin, mode = resolve_origin(stream_size, stream_position, outputs)
    if origin is None or mode == MODE_UNKNOWN:
        return None

    # A downscaled preview maps back through the ratio.
    sx, sy = float(fx), float(fy)
    if frame_size and frame_size != stream_size and frame_size[0] and frame_size[1]:
        sx = fx * (stream_size[0] / frame_size[0])
        sy = fy * (stream_size[1] / frame_size[1])

    if not (0 <= sx < stream_size[0] and 0 <= sy < stream_size[1]):
        return None

    gx = int(origin[0] + sx)
    gy = int(origin[1] + sy)
    for o in outputs:
        if o.enabled and o.contains(gx, gy):
            return LogicalPoint(x=gx, y=gy, output=o.name)
    return None  # dead band


def dead_band_rects(
    stream_size: Optional[tuple[int, int]],
    stream_position: Optional[tuple[int, int]],
    outputs: Sequence[Output],
    *,
    step: int = 8,
) -> list[dict]:
    """Coarse map of frame regions covered by no output.

    Sampled on a grid — this is diagnostics for the UI overlay, not a
    hit-test. Use frame_to_logical() for decisions.
    """
    if stream_size is None:
        return []
    origin, mode = resolve_origin(stream_size, stream_position, outputs)
    if origin is None or mode == MODE_UNKNOWN:
        return []
    w, h = stream_size
    rects: list[dict] = []
    for o in outputs:
        if not o.enabled:
            continue
        rects.append(
            {
                "output": o.name,
                "frame_x": o.x - origin[0],
                "frame_y": o.y - origin[1],
                "width": o.width,
                "height": o.height,
            }
        )
    covered = 0
    total = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            total += 1
            gx, gy = origin[0] + x, origin[1] + y
            if any(o.enabled and o.contains(gx, gy) for o in outputs):
                covered += 1
    return [
        {
            "mode": mode,
            "origin": list(origin),
            "stream_size": list(stream_size),
            "output_rects": rects,
            "covered_fraction": (covered / total) if total else 0.0,
            "has_dead_band": covered < total,
        }
    ]


__all__ = [
    "MODE_BOUNDING_BOX",
    "MODE_SINGLE_OUTPUT",
    "MODE_STREAM_POSITION",
    "MODE_UNKNOWN",
    "LogicalPoint",
    "Output",
    "bounding_box",
    "dead_band_rects",
    "frame_to_logical",
    "parse_kscreen_outputs",
    "resolve_origin",
]
