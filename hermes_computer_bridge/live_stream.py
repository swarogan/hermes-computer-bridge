"""Supervisor for the long-lived portal ScreenCast helper.

One process owns one portal session, one PipeWire fd and one GStreamer
pipeline for its whole lifetime. This class owns THAT process: it starts it,
waits for it to prove itself live, reports honestly on it, and puts it down.

Two deliberate design choices:

* **Readiness is a protocol line, not a sleep.** The helper prints a JSON
  line the moment the first real frame hits disk. `start()` blocks on that,
  so a caller is never told "live" before a single pixel exists.
* **Supervision runs on `status()`, not on a background timer.** The pane
  polls status continuously, so that is already the heartbeat; keeping the
  restart decision on the caller's thread makes the backoff provable in a
  test instead of a race against a daemon thread.

The injected `clock` schedules restart backoff only. Waiting for readiness
uses real wall time on purpose — a fake clock that never advances would turn
a bounded wait into a hang.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from hermes_computer_bridge.frame_transport import frame_version

DEFAULT_READY_TIMEOUT_S = 45.0
DEFAULT_BACKOFF_S = 2.0
DEFAULT_MAX_RESTARTS = 5
# Enough to explain a failure, not enough to hoard a compositor's log spam.
DIAGNOSTIC_LINES = 20


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HELPER = REPO_ROOT / "helpers" / "portal_screencast.py"
DEFAULT_SYSTEM_PYTHON = "/usr/bin/python3"
DEFAULT_FPS = 10
DEFAULT_QUALITY = 75


def _proc_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            return handle.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _iter_pids() -> list[int]:
    try:
        return [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []


def reap_stale_helpers(
    output: Path,
    *,
    marker: str = "portal_screencast.py",
    iter_pids: Callable[[], Sequence[int]] = _iter_pids,
    cmdline: Callable[[int], str] = _proc_cmdline,
    kill: Callable[[int, int], None] = os.kill,
    sleep: Callable[[float], None] = time.sleep,
    exclude: Sequence[int] = (),
) -> list[int]:
    target = str(output)
    excluded = set(exclude)
    victims = []
    for pid in iter_pids():
        if pid in excluded:
            continue
        line = cmdline(pid)
        if marker in line and target in line:
            victims.append(pid)
    for pid in victims:
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if victims:
        sleep(0.5)
        for pid in victims:
            if marker in cmdline(pid):
                try:
                    kill(pid, signal.SIGKILL)
                except OSError:
                    pass
    return victims


class StreamNotReady(RuntimeError):
    """The helper never reported a live frame within the allowed window."""


def helper_command(
    *,
    output: Path,
    fps: int = DEFAULT_FPS,
    quality: int = DEFAULT_QUALITY,
    stream_index: Optional[int] = None,
    node_id: Optional[int] = None,
    timeout_s: int = 180,
    persist_mode: int = 2,
    system_python: str = DEFAULT_SYSTEM_PYTHON,
    helper: Path = DEFAULT_HELPER,
    enable_input: bool = False,
) -> list[str]:
    """argv for the long-lived helper.

    Neither `--stream-index` nor `--node-id` is passed when the caller did not
    choose one: the helper's own documented default decides, so a monitor is
    never silently hardcoded here.
    """
    argv = [
        system_python,
        str(helper),
        "stream",
        "--output",
        str(output),
        "--fps",
        str(fps),
        "--quality",
        str(quality),
        "--persist-mode",
        str(persist_mode),
        "--timeout",
        str(timeout_s),
    ]
    if node_id is not None:
        argv += ["--node-id", str(node_id)]
    elif stream_index is not None:
        argv += ["--stream-index", str(stream_index)]
    if enable_input:
        argv += ["--input"]
    return argv


class LiveStream:
    def __init__(
        self,
        *,
        command: Sequence[str],
        output: Path,
        spawn: Callable[..., Any] = subprocess.Popen,
        ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S,
        backoff_s: float = DEFAULT_BACKOFF_S,
        max_restarts: int = DEFAULT_MAX_RESTARTS,
        clock: Callable[[], float] = time.monotonic,
        reap: Callable[..., Any] = reap_stale_helpers,
    ) -> None:
        self.command = list(command)
        self.output = Path(output)
        self._spawn = spawn
        self._reap = reap
        self.ready_timeout_s = ready_timeout_s
        self.backoff_s = backoff_s
        self.max_restarts = max_restarts
        self._clock = clock

        self._lock = threading.RLock()
        self._process: Any = None
        self._reader: Optional[threading.Thread] = None
        self._ready = threading.Event()
        # Set when the child's stdout closes and it has been reaped, so a
        # readiness wait can end the moment success became impossible.
        self._gone = threading.Event()
        self._live_meta: dict[str, Any] = {}
        self._last_line: dict[str, Any] = {}
        self._diagnostics: list[str] = []
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._died_at: Optional[float] = None
        self._restarts = 0
        # False until someone asks for a stream; keeps `status()` from
        # spawning a helper nobody requested.
        self._want_running = False
        self._frame_version: Optional[str] = None

    # -- public API ---------------------------------------------------

    def start(self, ready_timeout_s: Optional[float] = None) -> dict[str, Any]:
        """Spawn the helper and block until it reports a real frame."""
        with self._lock:
            if self._alive():
                return dict(self._live_meta)
            self._want_running = True
            self._restarts = 0
            self._last_error = None
        return self._spawn_and_wait(ready_timeout_s or self.ready_timeout_s)

    def stop(self) -> None:
        """Terminate and reap. Idempotent; never followed by a restart."""
        with self._lock:
            self._want_running = False
            process = self._process
            self._process = None
            self._started_at = None
            self._died_at = None
            self._ready.clear()
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        else:
            process.wait()

    def is_running(self) -> bool:
        """Cheap, non-supervising liveness check.

        `status()` may decide to respawn, which blocks on a handshake. A
        caller on an event loop needs a way to ask without that risk.
        """
        with self._lock:
            return self._alive()

    def status(self) -> dict[str, Any]:
        """Report the truth, and supervise while we are here."""
        self._supervise()
        with self._lock:
            running = self._alive()
            line = dict(self._last_line)
            frames = int(line.get("frames") or 0)
            blank = bool(line.get("blank"))
            path = Path(self._live_meta.get("output") or self.output)
            # Read-only on purpose: `check_frame` owns the seen-marker. If
            # status() advanced it, a 2s poll would silently consume frames
            # the watcher is supposed to push to the socket.
            version = frame_version(path) if path.is_file() else None
            return {
                "running": running,
                "pid": self._process.pid if running and self._process else None,
                "fps": self._live_meta.get("fps"),
                "frames": frames,
                "blank": blank,
                # Same rule as the helper: blank is reported, never healthy.
                "healthy": running and frames > 0 and not blank,
                "frame_version": version,
                "frame_mtime": path.stat().st_mtime if version else None,
                "output": str(path),
                "uptime_s": (
                    round(self._clock() - self._started_at, 3)
                    if running and self._started_at is not None
                    else 0.0
                ),
                "restarts": self._restarts,
                "max_restarts": self.max_restarts,
                "last_error": self._last_error,
                "selected_stream": self._live_meta.get("selected_stream"),
                "streams": self._live_meta.get("streams", []),
                "diagnostics": list(self._diagnostics[-5:]),
            }

    def send(self, command: dict[str, Any]) -> bool:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return False
        stdin = getattr(process, "stdin", None)
        if stdin is None:
            return False
        try:
            stdin.write(json.dumps(command) + "\n")
            stdin.flush()
            return True
        except (BrokenPipeError, ValueError, OSError):
            return False

    def check_frame(self) -> Optional[str]:
        """The new frame version, or None if nothing changed since last look.

        This is what lets the socket push per FRAME rather than per manual
        capture: the helper rewrites one path, so a changed version IS a new
        frame. Returning the version (not just a flag) keeps the caller from
        having to re-stat the file it was just told about.
        """
        version = frame_version(self.output) if self.output.is_file() else None
        with self._lock:
            if version is None or version == self._frame_version:
                return None
            self._frame_version = version
            return version

    # -- internals ----------------------------------------------------

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _note_death(self) -> None:
        """Stamp the death once, so backoff is measured from the right point."""
        with self._lock:
            if self._process is None or self._alive():
                return
            if self._died_at is None:
                self._died_at = self._clock()

    def _supervise(self) -> None:
        with self._lock:
            if not self._want_running or self._alive():
                return
            self._note_death()
            if self._restarts >= self.max_restarts:
                return
            since = self._died_at if self._died_at is not None else self._clock()
            # Linear growth: a flapping helper waits longer each time instead
            # of hammering a broken compositor at a fixed rate.
            due = since + self.backoff_s * (self._restarts + 1)
            if self._clock() < due:
                return
            self._restarts += 1
        try:
            self._spawn_and_wait(self.ready_timeout_s)
        except StreamNotReady:
            # Recorded in _last_error by _spawn_and_wait; the next tick
            # decides whether any restarts remain.
            pass

    def _spawn_and_wait(self, ready_timeout_s: float) -> dict[str, Any]:
        try:
            self._reap(self.output)
        except Exception:
            pass
        with self._lock:
            self._ready.clear()
            self._gone.clear()
            self._live_meta = {}
            self._last_line = {}
            self._diagnostics = []
            self._died_at = None
            process = self._spawn(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._process = process
            self._started_at = self._clock()
            reader = threading.Thread(
                target=self._read_lines, args=(process,), daemon=True
            )
            self._reader = reader
        reader.start()

        # Real wall time: the injected clock only paces restarts. Poll in
        # slices so a helper that exits does not make the caller sit out the
        # whole consent window for an answer that can no longer arrive.
        deadline = time.monotonic() + ready_timeout_s
        while True:
            if self._ready.wait(timeout=min(0.1, max(0.0, deadline - time.monotonic()))):
                with self._lock:
                    self._last_error = None
                    return dict(self._live_meta)
            if self._gone.is_set() or time.monotonic() >= deadline:
                break

        detail = self._failure_detail(process, ready_timeout_s)
        with self._lock:
            self._last_error = detail
        self._kill(process)
        raise StreamNotReady(detail)

    def _failure_detail(self, process: Any, ready_timeout_s: float) -> str:
        with self._lock:
            reported = self._last_line.get("error")
            tail = " | ".join(self._diagnostics[-3:])
        code = process.poll()
        if reported:
            return str(reported)
        if code is not None:
            return f"helper exited {code} before reporting a live frame: {tail}"
        return f"no live frame within {ready_timeout_s}s: {tail}"

    def _kill(self, process: Any) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        else:
            process.wait()
        with self._lock:
            if self._process is process:
                self._process = None
                self._started_at = None

    def _read_lines(self, process: Any) -> None:
        """Drain the helper's stdout: parse protocol lines, keep the rest.

        Draining is not optional — an unread pipe eventually blocks the
        helper mid-stream, which would look exactly like a stalled portal.
        """
        stream = process.stdout
        if stream is not None:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                payload = None
                if line.startswith("{") and line.endswith("}"):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        payload = None
                if payload is None:
                    with self._lock:
                        self._diagnostics.append(line)
                        del self._diagnostics[:-DIAGNOSTIC_LINES]
                    continue
                with self._lock:
                    self._last_line = payload
                    if payload.get("error"):
                        self._last_error = str(payload["error"])
                    if payload.get("event") == "live":
                        self._live_meta = payload
                        self._ready.set()
        # EOF means the child's stdout closed: it is on its way out. Stamp
        # the death here so backoff starts at the death, not at whenever the
        # next status() poll happens to land.
        try:
            process.wait()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            if self._process is process:
                if self._died_at is None:
                    self._died_at = self._clock()
                self._gone.set()


__all__ = ["LiveStream", "StreamNotReady", "helper_command", "reap_stale_helpers"]
