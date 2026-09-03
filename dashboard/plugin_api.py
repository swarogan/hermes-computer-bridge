"""REST/socket namespace /api/plugins/hermes-computer-bridge/.

`ctx.rest` is JSON-only: Electron's bridge calls `fetchJson` and JSON.parse.
Therefore `/frame-data` returns a data URL in JSON — `FileResponse` would fail
as "Invalid JSON" before the renderer ever saw bytes. Polling `/status`
returns only a cheap version; the ~1.5 MiB data URL is fetched only when that
version changes. The socket is an accelerator over the same cache invariant,
never the only delivery path (OAuth remotes make ctx.socket a no-op).

The PRIMARY path is now a live stream: `/live/start` spawns one helper that
holds one portal session, one PipeWire fd and one pipeline, and rewrites one
JPEG atomically per frame. A watcher task notices each rewrite and pushes on
`/events`, so the socket fires per FRAME rather than per button press.
`/capture` stays only as an explicit single-shot escape hatch.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from hermes_computer_bridge.capture_service import CaptureService, read_outputs  # noqa: E402
from hermes_computer_bridge.errors import (  # noqa: E402
    CapabilityMissing,
    TransientError,
    UserCancelled,
)
from hermes_computer_bridge.broadcast import FrameBroadcaster, try_frame_message  # noqa: E402
from hermes_computer_bridge.geometry import frame_to_logical  # noqa: E402
from hermes_computer_bridge.input_protocol import input_calls  # noqa: E402
from hermes_computer_bridge.input_service import default_input_service  # noqa: E402
from hermes_computer_bridge import live_registry  # noqa: E402
from hermes_computer_bridge.targets import (  # noqa: E402
    delete_vnc_endpoint,
    find_vnc_endpoint,
    list_targets,
    parse_target,
    proxmox_config,
    save_vnc_endpoint,
    vnc_endpoints,
)
from hermes_computer_bridge.vnc_session import VncSession  # noqa: E402
from hermes_computer_bridge.live_stream import (  # noqa: E402
    DEFAULT_FPS,
    DEFAULT_QUALITY,
    LiveStream,
    StreamNotReady,
    helper_command,
)
from hermes_computer_bridge.frame_transport import (  # noqa: E402
    frame_data as encode_frame_data,
    frame_summary,
    latest_frame,
)

router = APIRouter()
EVIDENCE_DIR = PLUGIN_DIR / "evidence"
LIVE_FRAME = EVIDENCE_DIR / "live-frame.png"
LIVE_STREAM_FRAME = EVIDENCE_DIR / "live-frame.jpg"
_service = CaptureService()
_input_service = default_input_service()
_broadcaster = FrameBroadcaster()
_live: Optional[LiveStream] = None
_watcher: Optional[asyncio.Task] = None
_idle_stop: Optional[asyncio.Task] = None
_vnc: Optional[VncSession] = None
_target: str = "local"
_live_lock = asyncio.Lock()
IDLE_GRACE_S = 8.0
# Streams are only knowable inside an active portal session, so the last
# capture's stream list is cached to answer /status between sessions. It is a
# CACHE, never a claim about the current session.
_last_capture: dict[str, Any] = {}


class CaptureRequest(BaseModel):
    stream_index: Optional[int] = None
    node_id: Optional[int] = None
    timeout_s: int = 180
    output: Optional[str] = None


class LiveStartRequest(BaseModel):
    stream_index: Optional[int] = None
    node_id: Optional[int] = None
    fps: int = DEFAULT_FPS
    quality: int = DEFAULT_QUALITY
    timeout_s: int = 180
    target: Optional[str] = None
    control: bool = False


class _VncAgentSender:
    def __init__(self, vnc: VncSession, loop: asyncio.AbstractEventLoop) -> None:
        self._vnc = vnc
        self._loop = loop

    def is_running(self) -> bool:
        return self._vnc.is_running()

    def send(self, cmd: dict) -> bool:
        try:
            future = asyncio.run_coroutine_threadsafe(self._vnc.send(cmd), self._loop)
            return bool(future.result(timeout=5))
        except Exception:  # noqa: BLE001
            return False


def _resolve_output(name: Optional[str]) -> Path:
    if not name:
        return LIVE_FRAME
    candidate = (EVIDENCE_DIR / Path(name).name).resolve()
    if EVIDENCE_DIR.resolve() not in candidate.parents:
        raise HTTPException(status_code=400, detail="output must stay in evidence/")
    return candidate


async def _broadcast_frame(path: Path) -> bool:
    message = await asyncio.to_thread(try_frame_message, path)
    if message is None:
        return False
    await _broadcaster.send(message)
    return True


def live_status() -> dict[str, Any]:
    """Live-stream truth for both /live/status and the /status fallback."""
    if _vnc is not None and _vnc.is_running():
        return {
            "running": True,
            "pid": None,
            "healthy": _vnc.last_error is None and _vnc.frames > 0,
            "frames": _vnc.frames,
            "blank": False,
            "restarts": 0,
            "last_error": _vnc.last_error,
            "streams": [],
            "subscribers": _broadcaster.count(),
            "target": _target,
            "vm": _vnc.info,
        }
    if _live is None:
        return {
            "running": False,
            "pid": None,
            "healthy": False,
            "frames": 0,
            "blank": None,
            "restarts": 0,
            "last_error": None,
            "streams": [],
            "subscribers": _broadcaster.count(),
            "target": _target,
        }
    body = _live.status()
    body["subscribers"] = _broadcaster.count()
    body["target"] = _target
    return body


async def _watch_frames(interval_s: float) -> None:
    """Push on EVERY new frame, not once per manual capture.

    The helper rewrites one path atomically, so a changed frame version IS a
    new frame. `check_frame` stats a file — cheap, but still blocking, so it
    goes through a worker thread rather than stalling the gateway's loop.
    """
    try:
        while True:
            stream = _live
            if stream is None:
                return
            version = await asyncio.to_thread(stream.check_frame)
            if version:
                await _broadcast_frame(stream.output)
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        raise


async def _cancel_watcher() -> None:
    global _watcher
    task, _watcher = _watcher, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _stop_stream() -> dict[str, Any]:
    global _vnc
    await _cancel_watcher()
    if _live is not None:
        await asyncio.to_thread(_live.stop)
    if _vnc is not None:
        await _vnc.stop()
        _vnc = None
    live_registry.set_current(None)
    body = live_status()
    await _broadcaster.send({"type": "stream", **body})
    return body


def _cancel_idle_stop() -> None:
    global _idle_stop
    task, _idle_stop = _idle_stop, None
    if task is not None:
        task.cancel()


async def _idle_stop_after_grace() -> None:
    try:
        await asyncio.sleep(IDLE_GRACE_S)
    except asyncio.CancelledError:
        return
    async with _live_lock:
        live_up = _live is not None and _live.is_running()
        vnc_up = _vnc is not None and _vnc.is_running()
        if _broadcaster.count() == 0 and (live_up or vnc_up):
            await _stop_stream()


def _schedule_idle_stop() -> None:
    global _idle_stop
    if _broadcaster.count() > 0:
        return
    live_up = _live is not None and _live.is_running()
    vnc_up = _vnc is not None and _vnc.is_running()
    if not (live_up or vnc_up):
        return
    _cancel_idle_stop()
    _idle_stop = asyncio.create_task(_idle_stop_after_grace())


@router.post("/live/start")
async def live_start(req: LiveStartRequest) -> dict[str, Any]:
    """Open the stream for the selected target: local portal or a Proxmox VM."""
    async with _live_lock:
        return await _start_locked(req)


async def _start_locked(req: LiveStartRequest) -> dict[str, Any]:
    global _live, _watcher, _vnc, _target
    _cancel_idle_stop()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    target = parse_target(req.target)
    _target = target["id"]
    live_registry.set_panel_target(_target)

    if target["kind"] in ("vm", "vnc"):
        if target["kind"] == "vm" and not proxmox_config():
            raise HTTPException(status_code=501, detail="Proxmox not configured")
        if _live is not None:
            await asyncio.to_thread(_live.stop)
            _live = None
        await _cancel_watcher()
        if _vnc is not None:
            await _vnc.stop()
        _vnc = VncSession(
            descriptor=target,
            output=LIVE_STREAM_FRAME,
            on_frame=_broadcast_frame,
            fps=req.fps,
        )
        try:
            info = await _vnc.start()
        except Exception as exc:  # noqa: BLE001
            _vnc = None
            raise HTTPException(status_code=503, detail=f"VNC connect failed: {exc}") from exc
        live_registry.set_current(_VncAgentSender(_vnc, asyncio.get_running_loop()))
        await _broadcast_frame(LIVE_STREAM_FRAME)
        return {"ok": True, "stream": info, "target": _target, "live": live_status()}

    if _vnc is not None:
        await _vnc.stop()
        _vnc = None
    if _live is None or not _live.is_running():
        _live = LiveStream(
            command=helper_command(
                output=LIVE_STREAM_FRAME,
                fps=req.fps,
                quality=req.quality,
                stream_index=req.stream_index,
                node_id=req.node_id,
                timeout_s=req.timeout_s,
                enable_input=False,
            ),
            output=LIVE_STREAM_FRAME,
            ready_timeout_s=req.timeout_s + 30,
        )
    live_registry.set_current(None)
    try:
        meta = await asyncio.to_thread(_live.start)
    except StreamNotReady as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await _cancel_watcher()
    fps = int(meta.get("fps") or req.fps) or DEFAULT_FPS
    _watcher = asyncio.create_task(_watch_frames(1.0 / fps))
    await _broadcast_frame(_live.output)
    return {"ok": True, "stream": meta, "target": _target, "live": live_status()}


@router.post("/live/stop")
async def live_stop() -> dict[str, Any]:
    """Never leave a PipeWire pipeline running after the pane goes away."""
    _cancel_idle_stop()
    async with _live_lock:
        body = await _stop_stream()
    return {"ok": True, "live": body}


@router.get("/targets")
async def targets() -> dict[str, Any]:
    return {"targets": await asyncio.to_thread(list_targets), "selected": _target}


def _bindings_file() -> Path:
    from hermes_computer_bridge.targets import config_file

    return config_file().parent / "bindings.json"


def _load_bindings() -> dict:
    import json

    try:
        return json.loads(_bindings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_bindings(data: dict) -> None:
    import json

    path = _bindings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_proxmox_file() -> dict:
    import json
    from hermes_computer_bridge.targets import config_file

    try:
        return json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_proxmox_file(url: str, token: str, node: str) -> None:
    import json
    import os as _os
    from hermes_computer_bridge.targets import config_file

    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "token": token, "node": node}), encoding="utf-8")
    try:
        _os.chmod(path, 0o600)
    except OSError:
        pass


@router.get("/config/proxmox")
async def get_proxmox() -> dict[str, Any]:
    data = await asyncio.to_thread(_read_proxmox_file)
    return {
        "url": data.get("url", ""),
        "node": data.get("node", ""),
        "has_token": bool(data.get("token")),
    }


@router.post("/config/proxmox")
async def set_proxmox(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    url = str(body.get("url") or "").strip()
    node = str(body.get("node") or "").strip()
    token = body.get("token")
    if not url or not node:
        raise HTTPException(status_code=400, detail="url and node are required")
    existing = await asyncio.to_thread(_read_proxmox_file)
    resolved_token = str(token).strip() if token else existing.get("token")
    if not resolved_token:
        raise HTTPException(status_code=400, detail="token is required")
    await asyncio.to_thread(_write_proxmox_file, url, resolved_token, node)
    return {"ok": True, "targets": await asyncio.to_thread(list_targets)}


@router.get("/config/vnc")
async def get_vnc() -> dict[str, Any]:
    endpoints = await asyncio.to_thread(vnc_endpoints)
    return {
        "endpoints": [
            {
                "id": e["id"],
                "label": e.get("label", ""),
                "host": e.get("host", ""),
                "port": e.get("port", 5900),
                "has_password": bool(e.get("password")),
            }
            for e in endpoints
        ]
    }


@router.post("/config/vnc")
async def set_vnc(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    host = str(body.get("host") or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="host is required")
    endpoint_id = str(body.get("id") or host).strip()
    password = str(body.get("password") or "")
    if not password:
        existing = await asyncio.to_thread(find_vnc_endpoint, endpoint_id)
        if existing:
            password = existing.get("password", "")
    endpoint = {
        "id": endpoint_id,
        "label": str(body.get("label") or endpoint_id).strip(),
        "host": host,
        "port": int(body.get("port") or 5900),
        "password": password,
    }
    await asyncio.to_thread(save_vnc_endpoint, endpoint)
    return {"ok": True, "targets": await asyncio.to_thread(list_targets)}


@router.delete("/config/vnc")
async def remove_vnc(id: str = Query(...)) -> dict[str, Any]:
    await asyncio.to_thread(delete_vnc_endpoint, id)
    return {"ok": True, "targets": await asyncio.to_thread(list_targets)}


@router.get("/binding")
async def get_bindings() -> dict[str, Any]:
    return {"bindings": await asyncio.to_thread(_load_bindings)}


@router.post("/binding")
async def set_binding(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    profile = str(body.get("profile") or "default")
    target = str(body.get("target") or "local")
    parse_target(target)
    bindings = await asyncio.to_thread(_load_bindings)
    bindings[profile] = target
    await asyncio.to_thread(_save_bindings, bindings)
    return {"ok": True, "bindings": bindings}


@router.post("/input")
async def live_input(cmd: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        input_calls(cmd, 0)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if _vnc is not None and _vnc.is_running():
        if not await _vnc.send(cmd):
            raise HTTPException(status_code=503, detail="input not delivered")
        return {"ok": True, "rung": f"proxmox-vnc:{_target}"}
    # A remote target must NEVER fall through to the local input ladder. The
    # panel still shows the VM, so the human keeps aiming at it while the
    # pointer is really being driven on their own desktop — with VM-sized
    # coordinates landing somewhere else entirely on a multi-monitor host.
    # Reported as "the cursor runs off to the other monitor and I cannot click".
    if _target and _target != "local":
        raise HTTPException(
            status_code=503,
            detail=(
                f"no live session for {_target}; refusing to send input to the "
                "local desktop instead. Reconnect the target in the Computer panel."
            ),
        )
    try:
        rung = await asyncio.to_thread(_input_service.inject, cmd)
    except CapabilityMissing as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {"ok": True, "rung": rung}


@router.post("/clipboard")
async def set_clipboard(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    text = str(body.get("text") or "")
    if _vnc is not None and _vnc.is_running():
        ok = await _vnc.set_clipboard(text)
        return {"ok": ok, "target": _target}
    return {"ok": False, "error": "no remote session"}


@router.get("/clipboard")
async def get_clipboard() -> dict[str, Any]:
    text = _vnc.get_clipboard() if _vnc is not None and _vnc.is_running() else ""
    return {"text": text}


@router.get("/live/status")
async def live_status_route() -> dict[str, Any]:
    return await asyncio.to_thread(live_status)


@router.get("/status")
async def status() -> dict[str, Any]:
    """Cheap polling response: probe + frame VERSION, never frame bytes."""
    try:
        body: dict[str, Any] = await asyncio.to_thread(_service.probe)
    except Exception as exc:  # noqa: BLE001
        body = {"ok": False, "error": str(exc)}
    body.update(frame_summary(latest_frame(EVIDENCE_DIR)))
    body["outputs"] = [o.to_dict() for o in await asyncio.to_thread(read_outputs)]
    live = await asyncio.to_thread(live_status)
    body["live"] = live
    # A running stream is the live truth; the capture cache only answers when
    # no stream is up. Never report a stale capture as the current session.
    if live["running"]:
        body["streams"] = live.get("streams") or []
        body["frame_blank"] = live.get("blank")
        body["streams_are_cached"] = False
    else:
        body["streams"] = _last_capture.get("streams", [])
        body["frame_blank"] = _last_capture.get("blank")
        body["streams_are_cached"] = bool(_last_capture)
    running = live["running"]
    body["target"] = _target
    agent = live_registry.agent_activity()
    body["agent_target"] = agent["target"]
    body["agent_seq"] = agent["seq"]
    body["input"] = {
        "selected": "portal-remotedesktop" if running else _input_service.selected_name(),
        "rungs": [{"rung": "portal-remotedesktop", "available": running}]
        + _input_service.status(),
    }
    return body


@router.get("/streams")
async def streams() -> dict[str, Any]:
    outputs = await asyncio.to_thread(read_outputs)
    return {
        "outputs": [o.to_dict() for o in outputs],
        "note": (
            "Portal streams are only known inside an active session; capture "
            "returns every stream it actually received."
        ),
    }


@router.post("/capture")
async def capture(req: CaptureRequest) -> dict[str, Any]:
    """Explicit single-shot escape hatch — NOT the primary path.

    The live stream is. This stays for diagnosing a portal that will start a
    session but not sustain one.
    """
    out = _resolve_output(req.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame, result = await asyncio.to_thread(
            _service.capture,
            out,
            stream_index=req.stream_index,
            node_id=req.node_id,
            timeout_s=req.timeout_s,
        )
    except UserCancelled as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CapabilityMissing as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except TransientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    attempts = [
        {"rung": a.rung, "ok": a.ok, "error": a.error, "kind": a.kind}
        for a in result.attempts
    ]
    if frame is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "no capture rung succeeded", "attempts": attempts},
        )
    body = await asyncio.to_thread(_service.describe_frame, frame)
    body["ok"] = True
    body["attempts"] = attempts
    body.update(frame_summary(frame.path))
    _last_capture["streams"] = [s.to_dict() for s in (frame.all_streams or [frame.stream])]
    _last_capture["blank"] = bool(frame.blank)
    await _broadcast_frame(frame.path)
    return body


@router.get("/frame-data")
async def frame_data(version: Optional[str] = None) -> dict[str, Any]:
    """JSON-safe PNG data URL of the newest frame.

    A single-file atomic stream can only ever serve the current frame, so a
    requested `version` is advisory: the fetch races the 10fps rewrite, and
    rejecting the mismatch floods the log without ever helping the client.
    """
    del version
    latest = latest_frame(EVIDENCE_DIR)
    if latest is None:
        raise HTTPException(status_code=404, detail="no captured frame")
    return await asyncio.to_thread(encode_frame_data, latest)


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    await websocket.accept()
    _broadcaster.add(websocket)
    _cancel_idle_stop()
    try:
        latest = latest_frame(EVIDENCE_DIR)
        await websocket.send_json(
            {"type": "ready", **frame_summary(latest), "live": live_status()}
        )
        while True:
            # The browser normally sends nothing. Receiving keeps disconnect
            # detection deterministic without heartbeat spam.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _broadcaster.discard(websocket)
        _schedule_idle_stop()


@router.get("/map")
async def map_point(
    x: int = Query(..., description="frame pixel x"),
    y: int = Query(..., description="frame pixel y"),
    width: int = Query(..., description="stream width"),
    height: int = Query(..., description="stream height"),
) -> dict[str, Any]:
    outputs = await asyncio.to_thread(read_outputs)
    point = frame_to_logical(
        x,
        y,
        stream_size=(width, height),
        stream_position=None,
        outputs=outputs,
    )
    return {
        "frame": {"x": x, "y": y},
        "logical": point.to_dict() if point else None,
        "in_dead_band": point is None,
        "outputs": [o.to_dict() for o in outputs],
        "note": "diagnostic only — step 3 does not inject input",
    }
