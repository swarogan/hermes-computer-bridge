"""Rung 1: xdg-desktop-portal ScreenCast.

One rung, every portal desktop: KDE, GNOME, COSMIC, Sway, Hyprland. There is
no DE branch here — that is the whole point of the ladder.

The actual D-Bus + PipeWire work lives in helpers/portal_screencast.py under
/usr/bin/python3, because the Hermes venv has no `gi` (a C binding, not a pip
install). This class drives that helper as a subprocess and translates its
exit codes into ladder semantics.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from hermes_computer_bridge.errors import (
    CapabilityMissing,
    TransientError,
    UserCancelled,
)
from hermes_computer_bridge.provider import CaptureProvider, Frame, StreamInfo

RUNG = "portal-screencast"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HELPER = REPO_ROOT / "helpers" / "portal_screencast.py"
DEFAULT_SYSTEM_PYTHON = "/usr/bin/python3"

# Helper exit codes (see helpers/portal_screencast.py)
EXIT_GI_MISSING = 2
EXIT_CREATE_SESSION = 3
EXIT_TIMEOUT = 4
EXIT_CANCELLED = 5
EXIT_RESPONSE = 6
EXIT_NO_STREAMS = 7
EXIT_PIPEWIRE_FD = 8


class PortalCapture(CaptureProvider):
    rung = RUNG

    def __init__(
        self,
        system_python: str = DEFAULT_SYSTEM_PYTHON,
        helper: Path = DEFAULT_HELPER,
        *,
        persist_mode: int = 2,
    ) -> None:
        self.system_python = system_python
        self.helper = Path(helper)
        self.persist_mode = persist_mode

    # -- internals ---------------------------------------------------

    def _preflight(self) -> None:
        if not self.helper.is_file():
            raise CapabilityMissing(self.rung, f"helper not found: {self.helper}")
        if not (
            os.path.isfile(self.system_python) and os.access(self.system_python, os.X_OK)
        ):
            resolved = shutil.which(self.system_python)
            if not resolved:
                raise CapabilityMissing(
                    self.rung, f"system python not executable: {self.system_python}"
                )
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.path.exists(
            f"/run/user/{os.getuid()}/bus"
        ):
            raise CapabilityMissing(self.rung, "no session D-Bus")

    def _run(self, args: list[str], timeout_s: int) -> tuple[int, dict[str, Any], str]:
        try:
            proc = subprocess.run(
                [self.system_python, str(self.helper), *args],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=str(REPO_ROOT),
                env=os.environ.copy(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransientError(
                self.rung, f"helper exceeded {timeout_s}s: {exc}"
            ) from exc

        stdout = (proc.stdout or "").strip()
        payload: dict[str, Any] = {}
        if stdout:
            # `spike` prints one compact JSON line; `probe` pretty-prints a
            # whole block. Try last-line first (cheap), then the whole blob.
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        payload = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            if not payload:
                start = stdout.find("{")
                if start != -1:
                    try:
                        payload = json.loads(stdout[start:])
                    except json.JSONDecodeError:
                        payload = {}
        return proc.returncode, payload, (proc.stderr or "")[-2000:]

    def _raise_for(self, code: int, payload: dict[str, Any], stderr: str) -> None:
        detail = payload.get("error") or stderr.strip().splitlines()[-1:] or ""
        if isinstance(detail, list):
            detail = detail[0] if detail else ""
        if code == EXIT_CANCELLED:
            raise UserCancelled(f"{self.rung}: {detail or 'consent dismissed'}")
        if code in (EXIT_GI_MISSING, EXIT_CREATE_SESSION):
            raise CapabilityMissing(self.rung, str(detail))
        if code in (EXIT_TIMEOUT, EXIT_RESPONSE, EXIT_NO_STREAMS, EXIT_PIPEWIRE_FD):
            raise TransientError(self.rung, f"exit {code}: {detail}")
        raise TransientError(self.rung, f"exit {code}: {detail}")

    # -- CaptureProvider ---------------------------------------------

    def probe(self) -> dict[str, Any]:
        self._preflight()
        code, payload, stderr = self._run(["probe"], timeout_s=20)
        if code != 0 or not payload.get("ok"):
            self._raise_for(code or 1, payload, stderr)
        sc = payload.get("screencast") or {}
        if not sc.get("version"):
            raise CapabilityMissing(self.rung, "portal ScreenCast interface absent")
        return {
            "rung": self.rung,
            "screencast_version": sc.get("version"),
            "source_types": sc.get("AvailableSourceTypes"),
            "cursor_modes": sc.get("AvailableCursorModes"),
            "remote_desktop": payload.get("remote_desktop"),
            "restore_token_present": payload.get("restore_token_present"),
            "session": payload.get("session"),
            "interpreter": payload.get("interpreter"),
        }

    def capture(
        self,
        output: Path,
        *,
        stream_index: Optional[int] = None,
        node_id: Optional[int] = None,
        timeout_s: int = 180,
    ) -> Frame:
        self._preflight()
        output = Path(output)
        args = [
            "spike",
            "-o",
            str(output),
            "--persist-mode",
            str(self.persist_mode),
            "--timeout",
            str(timeout_s),
        ]
        if node_id is not None:
            args += ["--node-id", str(node_id)]
        elif stream_index is not None:
            args += ["--stream-index", str(stream_index)]

        code, payload, stderr = self._run(args, timeout_s=timeout_s + 30)
        if code != 0 or not payload.get("ok"):
            self._raise_for(code or 1, payload, stderr)

        path = Path(payload.get("path") or output)
        if not path.is_file() or path.stat().st_size == 0:
            # Never hand back a Frame whose file is not there.
            raise TransientError(self.rung, f"helper reported ok but no file at {path}")

        streams = [
            StreamInfo.from_helper(s) for s in (payload.get("streams") or [])
        ]
        selected_raw = payload.get("selected_stream")
        selected = (
            StreamInfo.from_helper(selected_raw)
            if selected_raw
            else (streams[0] if streams else None)
        )
        if selected is None:
            raise TransientError(self.rung, "no stream metadata in helper payload")

        width, height = _png_size(path)
        return Frame(
            path=path,
            width=width,
            height=height,
            bytes_len=path.stat().st_size,
            stream=selected,
            rung=self.rung,
            all_streams=streams,
            stats=dict(payload.get("stats") or {}),
        )


def _png_size(path: Path) -> tuple[int, int]:
    """Read IHDR straight from the container. No image library needed."""
    with path.open("rb") as fh:
        head = fh.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise TransientError(RUNG, f"not a PNG: {path}")
    return (
        int.from_bytes(head[16:20], "big"),
        int.from_bytes(head[20:24], "big"),
    )


__all__ = ["PortalCapture", "RUNG"]
