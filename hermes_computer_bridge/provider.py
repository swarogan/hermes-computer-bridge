"""Provider ABCs.

One interface over every capture (and later input) backend, so the agent
tools are identical whether the frame comes from the portal locally, from
X11, or from a remote box. The ladder picks a provider; callers never
branch on desktop environment.

Step 2 implements capture only. Input ABC is declared so the shape is
fixed, but no rung is wired — `computer_bridge_click` still reports
capability-missing rather than pretending.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class StreamInfo:
    """One capture stream exactly as the backend described it.

    `position` is Optional on purpose: xdg-desktop-portal-kde does NOT
    populate it, so a provider that invents (0, 0) would silently produce
    wrong coordinates. None means "backend did not say".
    """

    node_id: int
    index: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    position: Optional[tuple[int, int]] = None
    source_type: Optional[int] = None
    id: Optional[str] = None
    mapping_id: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> Optional[tuple[int, int]]:
        if self.width is None or self.height is None:
            return None
        return (self.width, self.height)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "node_id": self.node_id,
            "width": self.width,
            "height": self.height,
            "size": list(self.size) if self.size else None,
            "position": list(self.position) if self.position else None,
            "source_type": self.source_type,
            "id": self.id,
            "mapping_id": self.mapping_id,
        }

    @classmethod
    def from_helper(cls, payload: dict[str, Any]) -> "StreamInfo":
        size = payload.get("size") or [None, None]
        pos = payload.get("position")
        width = size[0] if len(size) > 0 else None
        height = size[1] if len(size) > 1 else None
        position = (int(pos[0]), int(pos[1])) if pos and len(pos) >= 2 else None
        return cls(
            node_id=int(payload["node_id"]),
            index=int(payload.get("index", 0)),
            width=int(width) if width is not None else None,
            height=int(height) if height is not None else None,
            position=position,
            source_type=payload.get("source_type"),
            id=payload.get("id"),
            mapping_id=payload.get("mapping_id"),
            raw=dict(payload.get("props") or {}),
        )


@dataclass
class Frame:
    """A captured frame that actually exists on disk."""

    path: Path
    width: int
    height: int
    bytes_len: int
    stream: StreamInfo
    rung: str
    all_streams: list[StreamInfo] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def blank(self) -> bool:
        """True when the frame looks like an unrendered black rectangle."""
        return bool(self.stats.get("blank"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes_len,
            "rung": self.rung,
            "stats": self.stats,
            "blank": self.blank,
            "stream": self.stream.to_dict(),
            "streams": [s.to_dict() for s in self.all_streams],
            "stream_count": len(self.all_streams),
        }


class CaptureProvider(abc.ABC):
    """A rung on the capture ladder."""

    #: ladder rung identifier, e.g. "portal-screencast"
    rung: str = "unknown"

    @abc.abstractmethod
    def probe(self) -> dict[str, Any]:
        """Cheap read-only check. Must not open a session or prompt.

        Raise CapabilityMissing when this rung cannot work here.
        """

    @abc.abstractmethod
    def capture(
        self,
        output: Path,
        *,
        stream_index: Optional[int] = None,
        node_id: Optional[int] = None,
        timeout_s: int = 180,
    ) -> Frame:
        """Write ONE frame to *output* and return it.

        Must raise rather than return a Frame whose file does not exist.
        """

    def list_streams(self) -> Sequence[StreamInfo]:
        """Streams known without capturing. Empty when the backend only
        reveals them during a session (the portal does)."""
        return ()


class InputProvider(abc.ABC):
    """A rung on the input ladder. Declared, not implemented in step 2."""

    rung: str = "unknown"

    @abc.abstractmethod
    def probe(self) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def click(self, x: int, y: int, button: str = "left") -> None:
        ...

    @abc.abstractmethod
    def type_text(self, text: str) -> None:
        ...

    @abc.abstractmethod
    def key(self, combo: str) -> None:
        ...


__all__ = [
    "CaptureProvider",
    "Frame",
    "InputProvider",
    "StreamInfo",
]
