"""Frame fan-out to whatever sockets are currently attached.

Deliberately knows nothing about FastAPI: subscribers are duck-typed on
`await send_json(dict)`. That keeps the fan-out testable under the system
interpreter (which has no FastAPI) and keeps the delivery rule in one place.

The socket is always an ACCELERATOR. `ctx.socket` is a no-op on OAuth
remotes, so zero subscribers is a normal state, not an error — the pane's
polling fallback is what guarantees delivery.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from hermes_computer_bridge.frame_transport import InvalidFrame, frame_data, frame_summary


def frame_message(path: Path) -> dict[str, Any]:
    """Build one self-contained live frame for the JSON socket transport."""
    return {"type": "frame", **frame_summary(path), **frame_data(path)}


def try_frame_message(path: Path) -> Optional[dict[str, Any]]:
    """Build a frame message, or skip a transient invalid/disappearing frame."""
    try:
        return frame_message(path)
    except (InvalidFrame, OSError):
        return None


class FrameBroadcaster:
    def __init__(self) -> None:
        self._sockets: set[Any] = set()
        self._lock = threading.Lock()

    def add(self, socket: Any) -> None:
        with self._lock:
            self._sockets.add(socket)

    def discard(self, socket: Any) -> None:
        with self._lock:
            self._sockets.discard(socket)

    def count(self) -> int:
        with self._lock:
            return len(self._sockets)

    async def send(self, message: dict[str, Any]) -> int:
        """Deliver to every live subscriber; return how many got it.

        A socket that raises has gone away between our snapshot and the
        write. Dropping it is the point: otherwise every later frame pays
        for a corpse.
        """
        with self._lock:
            targets = tuple(self._sockets)
        delivered = 0
        dead = []
        for socket in targets:
            try:
                await socket.send_json(message)
                delivered += 1
            except Exception:  # noqa: BLE001 - any failure means it is gone
                dead.append(socket)
        if dead:
            with self._lock:
                for socket in dead:
                    self._sockets.discard(socket)
        return delivered


__all__ = ["FrameBroadcaster", "frame_message", "try_frame_message"]
