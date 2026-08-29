"""Supervisor contract, driven by a FAKE helper — no portal, no compositor.

The real helper needs a consent dialog and a running compositor, so it can
never run here. What CAN be verified headlessly is everything the supervisor
itself is responsible for: waiting for readiness instead of sleeping, reaping
the child, refusing to call a blank stream healthy, and bounding restarts so a
permanently broken desktop does not spin forever.

The fake helper is a real subprocess speaking the real stdout protocol, so
these are behavioural tests, not mock theatre.
"""

from __future__ import annotations

import os
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

from hermes_computer_bridge.live_stream import LiveStream, StreamNotReady, reap_stale_helpers

FAKE_HELPER = textwrap.dedent(
    '''
    """Speaks the portal_screencast `stream` stdout protocol, without a portal."""
    import argparse, json, os, sys, time

    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--mode", default="live")
    p.add_argument("--fps", type=int, default=10)
    p.add_argument("--blank", action="store_true")
    a = p.parse_args()

    def frame(n):
        tmp = a.output + ".part"
        with open(tmp, "wb") as fh:
            fh.write(b"\\xff\\xd8\\xff\\xe0" + bytes(str(n), "ascii") * 8 + b"\\xff\\xd9")
        os.replace(tmp, a.output)

    def line(event, frames, **extra):
        blank = bool(a.blank)
        print(json.dumps({
            "event": event, "frames": frames, "blank": blank,
            "healthy": frames > 0 and not blank,
            "stats": {"available": True, "blank": blank},
            "output": os.path.abspath(a.output),
            "fps": a.fps, "node_id": 52,
            "selected_stream": {"node_id": 52, "size": [2560, 1440]},
            "streams": [{"node_id": 52, "size": [2560, 1440]},
                        {"node_id": 53, "size": [1408, 1152]}],
            **extra,
        }), flush=True)

    if a.mode == "never-ready":
        while True:
            time.sleep(0.05)

    if a.mode == "die-before-ready":
        print(json.dumps({"ok": False, "error": "portal said no"}), flush=True)
        raise SystemExit(9)

    frame(1)
    line("live", 1)

    if a.mode == "die-after-ready":
        raise SystemExit(9)

    n = 1
    while True:
        n += 1
        frame(n)
        line("status", n)
        time.sleep(0.02)
    '''
)


class FakeClock:
    """Injected time: restart backoff must be provable without sleeping."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def helper_path(tmp_path: Path) -> Path:
    script = tmp_path / "fake_helper.py"
    script.write_text(FAKE_HELPER, encoding="utf-8")
    return script


@pytest.fixture
def make_stream(helper_path: Path, tmp_path: Path):
    created: list[LiveStream] = []

    def factory(mode: str = "live", **kwargs):
        output = tmp_path / "live-frame.jpg"
        stream = LiveStream(
            command=[
                sys.executable,
                str(helper_path),
                "--output",
                str(output),
                "--mode",
                mode,
                *(["--blank"] if kwargs.pop("blank", False) else []),
            ],
            output=output,
            ready_timeout_s=kwargs.pop("ready_timeout_s", 10.0),
            reap=kwargs.pop("reap", lambda *a, **k: []),
            **kwargs,
        )
        created.append(stream)
        return stream

    yield factory
    for stream in created:
        stream.stop()


# --- readiness ---------------------------------------------------------


def test_start_waits_for_the_live_line_instead_of_sleeping_and_hoping(make_stream):
    stream = make_stream()

    meta = stream.start()

    assert meta["event"] == "live"
    assert meta["frames"] >= 1
    # The frame file exists BY THE TIME start() returns — that is the whole
    # point of a readiness line over a sleep().
    assert Path(meta["output"]).is_file()


def test_start_returns_every_stream_not_just_the_selected_one(make_stream):
    meta = make_stream().start()

    assert [s["node_id"] for s in meta["streams"]] == [52, 53]
    assert meta["selected_stream"]["node_id"] == 52


def test_start_gives_up_bounded_when_the_helper_never_becomes_live(make_stream):
    stream = make_stream("never-ready", ready_timeout_s=0.5)

    with pytest.raises(StreamNotReady):
        stream.start()

    # A helper that never went live must not be left running.
    assert stream.status()["running"] is False


def test_helper_dying_before_readiness_surfaces_its_error(make_stream):
    stream = make_stream("die-before-ready", ready_timeout_s=5.0)

    with pytest.raises(StreamNotReady) as excinfo:
        stream.start()

    assert "portal said no" in str(excinfo.value)


def test_a_dead_helper_ends_the_wait_immediately_not_at_the_timeout(make_stream):
    """Waiting out a 45s consent window for a process that already exited
    would make every failure feel like a hang."""
    stream = make_stream("die-before-ready", ready_timeout_s=30.0)

    began = time.monotonic()
    with pytest.raises(StreamNotReady):
        stream.start()
    elapsed = time.monotonic() - began

    assert elapsed < 5.0, f"waited {elapsed:.1f}s for an already-dead helper"


# --- status ------------------------------------------------------------


def test_status_reports_the_live_process(make_stream):
    stream = make_stream()
    stream.start()

    status = stream.status()

    assert status["running"] is True
    assert isinstance(status["pid"], int)
    assert status["fps"] == 10
    assert status["frames"] >= 1
    assert status["uptime_s"] >= 0
    assert status["frame_version"] is not None


def test_status_before_start_is_honest_not_optimistic(make_stream):
    status = make_stream().status()

    assert status["running"] is False
    assert status["pid"] is None
    assert status["healthy"] is False
    assert status["frames"] == 0


def test_a_blank_stream_is_reported_but_never_healthy(make_stream):
    stream = make_stream(blank=True)

    meta = stream.start()

    assert meta["blank"] is True
    status = stream.status()
    assert status["blank"] is True
    assert status["healthy"] is False, "a black rectangle is not a working stream"


def test_a_real_stream_is_healthy(make_stream):
    stream = make_stream()
    stream.start()

    assert stream.status()["healthy"] is True


# --- stop --------------------------------------------------------------


def test_stop_reaps_the_child_and_is_idempotent(make_stream):
    stream = make_stream()
    stream.start()
    pid = stream.status()["pid"]

    stream.stop()
    stream.stop()

    status = stream.status()
    assert status["running"] is False
    assert status["pid"] is None
    # Reaped, not merely signalled: a zombie would still answer kill(pid, 0).
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_stop_without_start_is_a_no_op(make_stream):
    make_stream().stop()


# --- restart -----------------------------------------------------------


def test_an_unexpected_death_is_restarted_after_the_backoff(make_stream):
    clock = FakeClock()
    stream = make_stream("live", clock=clock, backoff_s=5.0)
    stream.start()
    first_pid = stream.status()["pid"]

    stream._process.kill()
    stream._process.wait()

    # Immediately after the death the backoff has not elapsed: no respawn yet.
    assert stream.status()["running"] is False
    clock.advance(6.0)
    restarted = stream.status()

    assert restarted["running"] is True
    assert restarted["pid"] != first_pid
    assert restarted["restarts"] == 1


def test_a_deliberate_stop_is_never_restarted(make_stream):
    clock = FakeClock()
    stream = make_stream("live", clock=clock, backoff_s=1.0)
    stream.start()

    stream.stop()
    clock.advance(600.0)

    assert stream.status()["running"] is False
    assert stream.status()["restarts"] == 0


def test_a_permanently_broken_environment_stops_spinning(make_stream):
    """Bounded restarts: a dead desktop must not spawn helpers forever."""
    clock = FakeClock()
    stream = make_stream(
        "die-before-ready", clock=clock, backoff_s=1.0, max_restarts=2,
        ready_timeout_s=5.0,
    )
    with pytest.raises(StreamNotReady):
        stream.start()

    for _ in range(10):
        clock.advance(100.0)
        stream.status()

    status = stream.status()
    assert status["restarts"] == 2
    assert status["running"] is False
    assert "portal said no" in str(status["last_error"])


def test_backoff_grows_so_a_flapping_helper_backs_off(make_stream):
    clock = FakeClock()
    stream = make_stream("die-after-ready", clock=clock, backoff_s=2.0, max_restarts=5)
    stream.start()

    # Observe each death before moving the clock: a supervisor can only date
    # a death from when it looked, so observing first is what makes the
    # backoff arithmetic deterministic rather than a race with the reader.
    stream._process.wait()
    assert stream.status()["running"] is False
    clock.advance(2.5)  # past 2s -> first restart
    assert stream.status()["restarts"] == 1

    # Second failure must wait LONGER than the first, not the same 2s.
    stream._process.wait()
    assert stream.status()["restarts"] == 1
    clock.advance(2.5)  # 2.5s < the now-4s window
    assert stream.status()["restarts"] == 1, "backoff did not grow"
    clock.advance(10.0)
    assert stream.status()["restarts"] == 2


# --- per-frame notification -------------------------------------------


def test_every_new_frame_is_reported_once_not_only_a_manual_capture(make_stream):
    seen: list[str] = []
    stream = make_stream()
    stream.start()

    # Bounded polling (2s budget for ~3 frames the fake emits every 20ms),
    # not a timing assumption about any single tick.
    for _ in range(200):
        version = stream.check_frame()
        if version is not None:
            seen.append(version)
        if len(seen) >= 3:
            break
        time.sleep(0.01)

    assert len(seen) >= 3, "the socket would only ever fire on manual capture"
    assert len(set(seen)) == len(seen), "same frame reported twice"


def test_a_status_poll_does_not_swallow_a_frame_from_the_watcher(make_stream):
    """/status is read every 2s; the watcher pushes at fps. If reading status
    consumed the change, that frame would never reach the socket."""
    stream = make_stream("die-after-ready")
    stream.start()
    stream._process.wait()

    stream.status()

    assert stream.check_frame() is not None, "/status ate the watcher's frame"


def test_check_frame_is_silent_when_nothing_changed(make_stream):
    stream = make_stream("die-after-ready")
    stream.start()
    stream._process.wait()

    assert stream.check_frame() is not None, "the first frame is new"
    assert stream.check_frame() is None, "an unchanged file is not a new frame"


# --- the command that actually reaches the portal helper ---------------


def test_helper_command_targets_the_stream_subcommand_under_system_python(tmp_path):
    from hermes_computer_bridge.live_stream import helper_command

    argv = helper_command(output=tmp_path / "live-frame.jpg", fps=12, quality=70)

    assert argv[0] == "/usr/bin/python3", "gi is a C binding; the venv has none"
    assert argv[1].endswith("helpers/portal_screencast.py")
    assert argv[2] == "stream", "spike is the one-shot escape hatch, not this"
    assert "--fps" in argv and "12" in argv
    assert "--quality" in argv and "70" in argv
    assert "--persist-mode" in argv and "2" in argv


def test_helper_command_passes_an_explicit_monitor_choice_through(tmp_path):
    from hermes_computer_bridge.live_stream import helper_command

    by_node = helper_command(output=tmp_path / "f.jpg", node_id=53)
    by_index = helper_command(output=tmp_path / "f.jpg", stream_index=1)

    assert "--node-id" in by_node and "53" in by_node
    assert "--stream-index" in by_index and "1" in by_index


def test_helper_command_never_hardcodes_a_monitor_when_none_was_asked_for(tmp_path):
    from hermes_computer_bridge.live_stream import helper_command

    argv = helper_command(output=tmp_path / "f.jpg")

    assert "--node-id" not in argv
    assert "--stream-index" not in argv, "the helper's documented default decides"


def test_is_running_never_triggers_a_restart(make_stream):
    """The API asks this on the event loop thread; supervising there would
    respawn a helper synchronously and freeze the whole gateway."""
    clock = FakeClock()
    stream = make_stream("live", clock=clock, backoff_s=1.0)
    stream.start()
    stream._process.kill()
    stream._process.wait()
    clock.advance(600.0)

    assert stream.is_running() is False
    assert stream._restarts == 0, "is_running() respawned the helper"


def test_reap_kills_only_helpers_on_the_same_output():
    procs = {
        10: "/usr/bin/python3 helpers/portal_screencast.py stream --output /x/live-frame.jpg --fps 10",
        11: "/usr/bin/python3 helpers/portal_screencast.py stream --output /other/frame.jpg",
        12: "/usr/bin/python3 unrelated.py --output /x/live-frame.jpg",
        13: "",
    }
    killed = []
    victims = reap_stale_helpers(
        Path("/x/live-frame.jpg"),
        iter_pids=lambda: list(procs),
        cmdline=lambda pid: procs.get(pid, ""),
        kill=lambda pid, sig: killed.append((pid, sig)),
        sleep=lambda _s: None,
    )
    assert victims == [10]
    assert (10, signal.SIGTERM) in killed
    assert (11, signal.SIGTERM) not in killed
    assert (12, signal.SIGTERM) not in killed


def test_reap_escalates_to_sigkill_only_while_still_the_helper():
    line = "python3 helpers/portal_screencast.py stream --output /x/f.jpg"
    state = {10: line, 20: line}
    killed = []

    def kill(pid, sig):
        killed.append((pid, sig))
        if pid == 10 and sig == signal.SIGTERM:
            state[10] = ""

    reap_stale_helpers(
        Path("/x/f.jpg"),
        iter_pids=lambda: list(state),
        cmdline=lambda pid: state.get(pid, ""),
        kill=kill,
        sleep=lambda _s: None,
    )
    assert (10, signal.SIGKILL) not in killed
    assert (20, signal.SIGKILL) in killed


def test_reap_excludes_given_pids():
    killed = []
    victims = reap_stale_helpers(
        Path("/x/f.jpg"),
        iter_pids=lambda: [10],
        cmdline=lambda pid: "portal_screencast.py stream --output /x/f.jpg",
        kill=lambda pid, sig: killed.append(pid),
        sleep=lambda _s: None,
        exclude=(10,),
    )
    assert victims == []
    assert killed == []


def test_start_reaps_stale_helpers_before_spawning(make_stream):
    reaped = []
    stream = make_stream("live", reap=lambda output, **k: reaped.append(output) or [])
    stream.start()
    assert reaped == [stream.output]


def test_send_writes_only_to_a_running_helper(make_stream):
    stream = make_stream()
    assert stream.send({"op": "move", "x": 1, "y": 2}) is False
    stream.start()
    assert stream.send({"op": "move", "x": 1, "y": 2}) is True
    stream.stop()
    assert stream.send({"op": "move", "x": 1, "y": 2}) is False
