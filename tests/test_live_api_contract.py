"""Lifecycle + broadcast contract for dashboard/plugin_api.py.

FastAPI is not importable under the system interpreter (the helper's `gi`
lives there, the web stack does not), so this module is asserted at source
level — the same convention the existing API tests use. The logic worth
executing was deliberately pushed into the package instead, where
test_live_stream_supervisor.py and test_frame_broadcast.py run it for real.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_PATH = ROOT / "dashboard" / "plugin_api.py"
API = API_PATH.read_text(encoding="utf-8")
TREE = ast.parse(API)


def _routes() -> dict[str, str]:
    """route path -> decorator kind, read from the AST not from a regex."""
    found: dict[str, str] = {}
    for node in ast.walk(TREE):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            target = dec.func.value
            if not (isinstance(target, ast.Name) and target.id == "router"):
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant):
                found[dec.args[0].value] = dec.func.attr
    return found


def test_the_stream_has_an_explicit_lifecycle():
    routes = _routes()
    assert routes.get("/live/start") == "post"
    assert routes.get("/live/stop") == "post"
    assert routes.get("/live/status") == "get"


def test_nothing_that_already_worked_was_dropped():
    routes = _routes()
    for path, kind in (
        ("/status", "get"),
        ("/streams", "get"),
        ("/capture", "post"),
        ("/frame-data", "get"),
        ("/map", "get"),
        ("/events", "websocket"),
    ):
        assert routes.get(path) == kind, path


def test_capture_survives_only_as_an_escape_hatch_not_the_primary_path():
    assert "single-shot" in API or "escape hatch" in API
    assert "_live" in API, "the live supervisor, not /capture, is the main path"


def test_frames_reach_the_socket_from_the_stream_not_from_a_manual_capture():
    """The whole point of step 3: /events pushes because a FRAME landed."""
    assert "check_frame" in API, "no per-frame watcher = capture-button behaviour"
    assert "FrameBroadcaster" in API
    # The watcher must be paced by the stream's own fps, not a fixed guess.
    assert "fps" in API


def test_the_watcher_is_torn_down_with_the_stream():
    assert "/live/stop" in API
    assert "cancel()" in API, "a watcher outliving the stream leaks a task"


def test_status_still_answers_the_polling_fallback_with_live_state():
    """ctx.socket is a no-op on OAuth remotes; /status must carry the truth."""
    assert '"live"' in API
    assert "live_status" in API or "_live.status" in API


def test_transport_stays_json_only():
    assert "from fastapi.responses import FileResponse" not in API
    assert "import FileResponse" not in API
    assert "encode_frame_data" in API


def test_input_endpoint_validates_and_forwards():
    assert _routes().get("/input") == "post"
    assert "input_calls(cmd" in API
    assert "_input_service.inject" in API
    for banned in ("NotifyPointer", "NotifyKeyboard", "libei", "ydotool"):
        assert banned not in API, f"the backend forwards, it does not inject: {banned}"


def test_the_helper_is_driven_through_the_shared_command_builder():
    assert "helper_command" in API, "argv must not be hand-assembled twice"


def test_blocking_supervisor_calls_do_not_stall_the_event_loop():
    """start()/stop() wait on a subprocess; on the loop thread that freezes
    every other plugin served by the same gateway."""
    for call in ("_live.start", "_live.stop"):
        index = API.find(call)
        assert index != -1, call
        window = API[max(0, index - 200) : index]
        assert "to_thread" in window, f"{call} must not run on the event loop"


def _func_src(name: str) -> str:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(API, node) or ""
    raise AssertionError(f"function {name} not found")


def test_stream_auto_stops_when_the_last_socket_viewer_leaves():
    events = _func_src("events")
    assert "_broadcaster.discard" in events
    assert "_schedule_idle_stop()" in events

    schedule = _func_src("_schedule_idle_stop")
    assert "_broadcaster.count() > 0" in schedule
    assert "_live.is_running()" in schedule

    grace = _func_src("_idle_stop_after_grace")
    assert "_broadcaster.count() == 0" in grace
    assert "_live.is_running()" in grace
    assert "IDLE_GRACE_S" in grace


def test_a_returning_viewer_cancels_a_pending_idle_stop():
    assert "_cancel_idle_stop()" in _func_src("_start_locked")
    events = _func_src("events")
    assert events.index("_cancel_idle_stop()") > events.index("_broadcaster.add")


def test_idle_stop_is_reachable_only_from_the_events_disconnect():
    for name in ("live_start", "live_stop"):
        assert "_schedule_idle_stop" not in _func_src(name)


def test_input_routes_to_vnc_for_vm_and_the_input_service_for_local():
    src = _func_src("live_input")
    assert "_vnc.send" in src
    assert "_input_service.inject" in src
    assert "proxmox-vnc" in src
    assert "CapabilityMissing" in src


def test_status_reports_the_input_rungs():
    src = _func_src("status")
    assert 'body["input"]' in src
    assert "_input_service.status()" in src


def test_targets_route_and_vm_routing_exist():
    assert _routes().get("/targets") == "get"
    start = _func_src("_start_locked")
    assert "parse_target" in start
    assert "VncSession" in start
    assert "_live_lock" in _func_src("live_start")
    inp = _func_src("live_input")
    assert "_vnc.send" in inp
    assert "proxmox-vnc" in inp


def test_per_bot_target_binding_is_persisted():
    assert '@router.get("/binding")' in API
    assert '@router.post("/binding")' in API
    assert "_save_bindings" in API
    assert "_load_bindings" in API


def test_proxmox_config_endpoints_mask_the_token():
    assert '@router.get("/config/proxmox")' in API
    assert '@router.post("/config/proxmox")' in API
    assert "_write_proxmox_file" in API
    get = _func_src("get_proxmox")
    assert '"has_token": bool(' in get
    assert '"token":' not in get


def test_input_never_falls_through_to_the_local_desktop():
    """A remote target must not silently drive the human's own machine.

    Reported as "the cursor runs off to the other monitor and I cannot click".
    When the VNC session was down, /input dropped to `_input_service.inject`,
    which drives THIS desktop through portal-remotedesktop — while the panel
    still showed the VM. VM-sized coordinates then landed somewhere else
    entirely on a multi-monitor host, so the pointer appeared to run away.

    Asserted at source level, like the rest of this module: the guard must sit
    between the VNC branch and the local ladder.
    """
    body = ast.get_source_segment(
        API,
        next(
            node
            for node in ast.walk(TREE)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "live_input"
        ),
    )
    assert "_input_service.inject" in body, "local injection still lives here"
    guard = body.index('_target != "local"')
    local = body.index("_input_service.inject")
    assert guard < local, "the remote-target guard must come BEFORE local injection"
    assert "503" in body[guard - 200 : local], "refusal must be an error, not a silent no-op"


def test_clipboard_also_refuses_without_a_remote_session():
    """Already correct — pinned so it stays that way."""
    body = ast.get_source_segment(
        API,
        next(
            node
            for node in ast.walk(TREE)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "set_clipboard"
        ),
    )
    assert "no remote session" in body
