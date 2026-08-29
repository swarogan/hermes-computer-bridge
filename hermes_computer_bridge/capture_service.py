"""The capture ladder as a service.

Callers ask for a frame. They never ask what desktop this is. Rungs below
the portal are declared but not implemented; each raises CapabilityMissing
so the ladder walks past them honestly instead of pretending.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional, Sequence

from hermes_computer_bridge.errors import CapabilityMissing
from hermes_computer_bridge.geometry import (
    Output,
    dead_band_rects,
    parse_kscreen_outputs,
    resolve_origin,
)
from hermes_computer_bridge.ladder import CAPTURE_LADDER, LadderResult, run_ladder
from hermes_computer_bridge.portal_capture import PortalCapture
from hermes_computer_bridge.provider import CaptureProvider, Frame


class NotImplementedRung(CaptureProvider):
    """A declared-but-unbuilt rung. Honest, not silent."""

    def __init__(self, rung: str, reason: str = "not implemented yet") -> None:
        self.rung = rung
        self.reason = reason

    def probe(self) -> dict[str, Any]:
        raise CapabilityMissing(self.rung, self.reason)

    def capture(self, output: Path, **kwargs: Any) -> Frame:
        raise CapabilityMissing(self.rung, self.reason)


def default_providers() -> dict[str, CaptureProvider]:
    return {
        "portal-screencast": PortalCapture(),
        "wlr-screencopy": NotImplementedRung("wlr-screencopy"),
        "x11-shm": NotImplementedRung("x11-shm"),
        "remote-rfb": NotImplementedRung("remote-rfb"),
    }


def read_outputs(timeout_s: int = 5) -> list[Output]:
    """Logical output geometry.

    Only consulted when a backend did not report stream position. This is a
    geometry lookup, not a capture branch — the capture rung is identical
    everywhere.
    """
    try:
        proc = subprocess.run(
            ["kscreen-doctor", "-o"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return parse_kscreen_outputs(proc.stdout or "")


class CaptureService:
    def __init__(
        self,
        providers: Optional[dict[str, CaptureProvider]] = None,
        ladder: Sequence[str] = CAPTURE_LADDER,
    ) -> None:
        self.providers = providers or default_providers()
        self.ladder = tuple(ladder)

    def probe(self) -> dict[str, Any]:
        """Which rung would serve, and what did each one say."""
        result: LadderResult = run_ladder(
            self.ladder,
            lambda rung: self.providers[rung].probe(),
            transient_retries=1,
        )
        return {
            "ok": result.ok,
            "rung": result.used_rung,
            "probe": result.value,
            "attempts": [
                {"rung": a.rung, "ok": a.ok, "error": a.error, "kind": a.kind}
                for a in result.attempts
            ],
            "ladder": list(self.ladder),
        }

    def capture(
        self,
        output: Path,
        *,
        stream_index: Optional[int] = None,
        node_id: Optional[int] = None,
        timeout_s: int = 180,
        transient_retries: int = 2,
    ) -> tuple[Optional[Frame], LadderResult]:
        result: LadderResult = run_ladder(
            self.ladder,
            lambda rung: self.providers[rung].capture(
                output,
                stream_index=stream_index,
                node_id=node_id,
                timeout_s=timeout_s,
            ),
            transient_retries=transient_retries,
        )
        return result.value, result

    def describe_frame(self, frame: Frame) -> dict[str, Any]:
        """Frame + how its pixels relate to real outputs."""
        outputs = read_outputs()
        origin, mode = resolve_origin(frame.stream.size, frame.stream.position, outputs)
        info = frame.to_dict()
        info["geometry"] = {
            "outputs": [o.to_dict() for o in outputs],
            "origin": list(origin) if origin else None,
            "origin_mode": mode,
            "position_reported_by_backend": frame.stream.position is not None,
            "regions": dead_band_rects(
                frame.stream.size, frame.stream.position, outputs
            ),
        }
        return info


__all__ = ["CaptureService", "NotImplementedRung", "default_providers", "read_outputs"]
