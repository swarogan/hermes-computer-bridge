"""Source contract for the LIVE viewer in desktop/plugin.js.

The renderer needs Electron, so these pin the invariants that break silently
in production: a pane that never starts the stream, a stream left running
after the pane closes, or a UI that says "live" with no frame behind it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")


def test_the_stream_starts_on_mount_without_a_button_press():
    assert "'/live/start'" in JS or '"/live/start"' in JS
    assert "useEffect" in JS


def test_the_stream_is_stopped_when_the_pane_goes_away():
    """A PipeWire pipeline outliving the pane is a leaked portal session."""
    assert "'/live/stop'" in JS or '"/live/stop"' in JS
    # The start effect must hand back a cleanup, not just fire and forget.
    assert re.search(r"return\s*\(\)\s*=>", JS), "no unmount cleanup"


def test_the_manual_capture_button_is_no_longer_the_interaction():
    assert "/capture" not in JS, "the capture button was the prosthesis"
    assert "Capturing" not in JS


def test_no_manual_control_button_agent_drives_local():
    assert "'Control'" not in JS, "local control is agent-driven via uinput, not a manual button"
    assert "setControlling" not in JS
    assert "'Disconnect'" not in JS, "the stream auto-starts; no separate connect button"


def test_connection_state_is_reported_honestly():
    for state in ("connecting", "live", "stalled", "error"):
        assert state in JS.lower(), state


def test_live_is_never_claimed_without_a_frame():
    """`running` alone is not liveness — the helper can be up with a dead
    pipeline. The label must depend on a frame actually having arrived."""
    assert "frame_version" in JS
    assert re.search(r"frames\b", JS), "the pane must read the frame counter"
    # The blank flag must reach the UI rather than being rendered as a frame.
    assert "blank" in JS


def test_frames_arrive_over_the_socket_and_polling_still_backs_it_up():
    assert "ctx.socket('/events'" in JS
    assert "message.data_url" in JS, "socket notifications still trigger slow REST fetches"
    assert "setPushedFrame" in JS, "socket pixels are not retained by the renderer"
    assert "refetchInterval: POLL_MS" in JS
    assert "/frame-data" in JS, "the no-op-socket fallback needs the poll path"
    assert "setInterval" not in JS


def test_the_pane_docks_above_cronjobs():
    """The live view is a docked pane in the right sidebar column, above
    Cronjobs, matching hermes-computer-viewer (Grok Computer) exactly — NOT a
    sub-slot inside the routines pane. 'hermesBots.routines.before' hid it
    from the Computer tab (this regressed once). The Cronjobs pane id
    'hermes-bots:routines' is verified against the host pane registry."""
    assert "hermesBots.routines.before" not in JS
    assert "area: PANES_AREA" in JS
    assert "'hermes-bots:routines'" in JS
    assert re.search(r"pos:\s*['\"]top['\"]", JS)


def test_per_monitor_crop_and_first_output_default_survive():
    assert "drawImage(image, sx, sy, sw, sh," in JS
    assert "setOutputName(outputs[0].name)" in JS
    assert "All screens" in JS
    assert "useState(undefined)" in JS


def test_remote_control_via_preview_local_is_view_only():
    assert "'/input'" in JS
    assert "state === 'live'" in JS
    assert "target !== 'local'" in JS
    for banned in ("/click", "/type", "/key", "RemoteDesktop", "NotifyPointer"):
        assert banned not in JS, banned


def test_a_failed_start_still_leaves_the_user_a_way_to_retry():
    """Declining the KDE consent dialog is a normal outcome, not a dead end.
    Collapsing to a full-pane ErrorState would remove the only control that
    can ask again."""
    failure = re.search(r"const failure = ([^\n]+)", JS)
    assert failure, "failure expression not found"
    assert "startError" not in failure.group(1), (
        "a start failure must not take the reconnect control down with it"
    )
    # It still has to be visible somewhere.
    assert "startError" in JS


def test_a_start_failure_is_surfaced_as_an_error_state_not_silence():
    assert re.search(r"failed:\s*startError", JS), "start error never reaches the state machine"
    assert "error:" in JS, "no error label for the failed state"


def test_config_form_is_reachable_from_the_target_picker():
    assert "__connect__" in JS
    assert "ConfigForm" in JS
    assert "/config/proxmox" in JS
