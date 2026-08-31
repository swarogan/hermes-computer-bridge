"""Hermes plugin: agent tools over the capture and input ladders.

Capture runs through the provider ABC. click/type/key inject through the live
portal RemoteDesktop stream when one is up, else the input-service ladder
(wlrctl / xdotool), else they report capability-missing honestly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from hermes_computer_bridge.capture_service import CaptureService  # noqa: E402
from hermes_computer_bridge.errors import (  # noqa: E402
    CapabilityMissing,
    TransientError,
    UserCancelled,
)
from hermes_computer_bridge.input_protocol import input_calls  # noqa: E402
from hermes_computer_bridge.input_service import default_input_service  # noqa: E402
from hermes_computer_bridge import live_registry  # noqa: E402
from hermes_computer_bridge.targets import list_targets, parse_target, proxmox_config  # noqa: E402
from hermes_computer_bridge.agent_vnc import AgentVnc  # noqa: E402

EVIDENCE_DIR = PLUGIN_DIR / "evidence"
DEFAULT_FRAME = EVIDENCE_DIR / "live-frame.png"

_TARGET_PROP = {
    "type": "string",
    "description": "'local' (default) or 'vm:<id>' (e.g. 'vm:112'). See computer_bridge_targets.",
}

_service = CaptureService()
_input_service = default_input_service()
_agent_vnc: "AgentVnc | None" = None


def _vnc() -> AgentVnc:
    global _agent_vnc
    if _agent_vnc is None:
        _agent_vnc = AgentVnc()
    return _agent_vnc


def _inject(cmd: dict[str, Any], target: Any = None) -> dict[str, Any]:
    try:
        input_calls(cmd, 0)
    except (ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "kind": "bad_request", "error": str(exc)}
    try:
        info = parse_target(target)
    except ValueError as exc:
        return {"ok": False, "kind": "bad_request", "error": str(exc)}
    live_registry.set_agent_target(info["id"])

    if info["kind"] in ("vm", "vnc"):
        if _vnc().send(info, cmd):
            return {"ok": True, "rung": info["id"]}
        return {"ok": False, "kind": "transient", "error": "remote input not delivered"}

    stream = live_registry.get_current()
    if stream is not None and stream.is_running():
        if stream.send(cmd):
            return {"ok": True, "rung": "remote"}
        return {"ok": False, "kind": "transient", "error": "input not delivered"}
    try:
        rung = _input_service.inject(cmd)
    except CapabilityMissing as exc:
        return {
            "ok": False,
            "kind": "missing",
            "rung": _input_service.selected_name(),
            "error": str(exc),
        }
    return {"ok": True, "rung": rung}


def _capture(params: dict[str, Any] | None) -> str:
    params = params or {}
    try:
        info = parse_target(params.get("target"))
    except ValueError as exc:
        return json.dumps({"ok": False, "kind": "bad_request", "error": str(exc)})
    live_registry.set_agent_target(info["id"])

    if info["kind"] in ("vm", "vnc"):
        safe = info["id"].replace(":", "-")
        out = Path(params.get("output") or (EVIDENCE_DIR / f"{safe}.jpg"))
        if not out.is_absolute():
            out = EVIDENCE_DIR / out.name
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _vnc().screenshot(info)
            width, height = _vnc().dimensions(info)
        except Exception as exc:
            return json.dumps({"ok": False, "kind": "transient", "error": str(exc)})
        out.write_bytes(data)
        return json.dumps(
            {
                "ok": True,
                "output": str(out),
                "width": width,
                "height": height,
                "target": info["id"],
            }
        )

    out = Path(params.get("output") or DEFAULT_FRAME)
    if not out.is_absolute():
        out = EVIDENCE_DIR / out.name
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame, result = _service.capture(
            out,
            stream_index=params.get("stream_index"),
            node_id=params.get("node_id"),
            timeout_s=int(params.get("timeout_s") or 180),
        )
    except UserCancelled as exc:
        return json.dumps({"ok": False, "kind": "cancelled", "error": str(exc)})
    except CapabilityMissing as exc:
        return json.dumps({"ok": False, "kind": "missing", "error": str(exc)})
    except TransientError as exc:
        return json.dumps({"ok": False, "kind": "transient", "error": str(exc)})

    attempts = [
        {"rung": a.rung, "ok": a.ok, "error": a.error, "kind": a.kind}
        for a in result.attempts
    ]
    if frame is None:
        return json.dumps(
            {"ok": False, "error": "no capture rung succeeded", "attempts": attempts}
        )
    body = _service.describe_frame(frame)
    body["ok"] = True
    body["attempts"] = attempts
    return json.dumps(body)


def register(ctx) -> None:  # noqa: ANN001
    ctx.register_tool(
        name="computer_bridge_screenshot",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_screenshot",
            "description": (
                "Capture one desktop frame via the capability ladder (portal "
                "ScreenCast first, then wlr/X11/remote). Returns the PNG path, "
                "the stream used, every stream offered, and how frame pixels "
                "map onto real outputs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "output": {
                        "type": "string",
                        "description": "PNG filename or absolute path.",
                    },
                    "stream_index": {
                        "type": "integer",
                        "description": "Which offered stream (monitor) to capture.",
                    },
                    "node_id": {
                        "type": "integer",
                        "description": "PipeWire node id; overrides stream_index.",
                    },
                    "timeout_s": {"type": "integer"},
                    "target": {
                        "type": "string",
                        "description": (
                            "'local' (default) for this desktop, or 'vm:<id>' "
                            "(e.g. 'vm:112') for a Proxmox VM. List with "
                            "computer_bridge_targets."
                        ),
                    },
                },
            },
        },
        handler=lambda params, **kwargs: _capture(params),
    )

    ctx.register_tool(
        name="computer_bridge_targets",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_targets",
            "description": (
                "List the desktops this bot can view and control: the local "
                "desktop plus every running Proxmox VM. Use the returned 'id' "
                "(e.g. 'vm:112') as the 'target' argument on the other tools."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda params, **kwargs: json.dumps({"targets": list_targets()}),
    )

    ctx.register_tool(
        name="computer_bridge_status",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_status",
            "description": (
                "Report which capture rung would serve and what each rung said. "
                "Does not open a session or prompt for consent."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda params, **kwargs: json.dumps(_service.probe()),
    )

    ctx.register_tool(
        name="computer_bridge_click",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_click",
            "description": (
                "Click at a captured-frame pixel on the live desktop (see "
                "computer_bridge_screenshot for the coordinate space). Needs a "
                "live portal RemoteDesktop stream, else an input rung."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "target": _TARGET_PROP,
                },
                "required": ["x", "y"],
            },
        },
        handler=lambda params, **kwargs: json.dumps(
            _inject(
                {
                    "op": "click",
                    "x": (params or {}).get("x"),
                    "y": (params or {}).get("y"),
                    "button": (params or {}).get("button", "left"),
                },
                (params or {}).get("target"),
            )
        ),
    )

    ctx.register_tool(
        name="computer_bridge_type",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_type",
            "description": "Type text into the focused window on the live desktop.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "target": _TARGET_PROP},
                "required": ["text"],
            },
        },
        handler=lambda params, **kwargs: json.dumps(
            _inject(
                {"op": "text", "text": (params or {}).get("text", "")},
                (params or {}).get("target"),
            )
        ),
    )

    ctx.register_tool(
        name="computer_bridge_key",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_key",
            "description": (
                "Press a key or chord on the live desktop, e.g. key='Return', or "
                "key='c' with mods=['ctrl']."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "mods": {"type": "array", "items": {"type": "string"}},
                    "target": _TARGET_PROP,
                },
                "required": ["key"],
            },
        },
        handler=lambda params, **kwargs: json.dumps(
            _inject(
                {
                    "op": "key",
                    "key": (params or {}).get("key"),
                    "mods": (params or {}).get("mods", []),
                },
                (params or {}).get("target"),
            )
        ),
    )

    ctx.register_tool(
        name="computer_bridge_move",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_move",
            "description": "Move the pointer to a captured-frame pixel without clicking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "target": _TARGET_PROP,
                },
                "required": ["x", "y"],
            },
        },
        handler=lambda params, **kwargs: json.dumps(
            _inject(
                {"op": "move", "x": (params or {}).get("x"), "y": (params or {}).get("y")},
                (params or {}).get("target"),
            )
        ),
    )

    ctx.register_tool(
        name="computer_bridge_scroll",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_scroll",
            "description": "Scroll the wheel. Positive dy scrolls down, negative up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dx": {"type": "number"},
                    "dy": {"type": "number"},
                    "target": _TARGET_PROP,
                },
            },
        },
        handler=lambda params, **kwargs: json.dumps(
            _inject(
                {
                    "op": "scroll",
                    "dx": (params or {}).get("dx", 0.0),
                    "dy": (params or {}).get("dy", 0.0),
                },
                (params or {}).get("target"),
            )
        ),
    )

    ctx.register_tool(
        name="computer_bridge_drag",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_drag",
            "description": (
                "Press at one captured-frame pixel, move to another, and release "
                "(drag). Coordinates are frame pixels."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_x": {"type": "integer"},
                    "from_y": {"type": "integer"},
                    "to_x": {"type": "integer"},
                    "to_y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "target": _TARGET_PROP,
                },
                "required": ["from_x", "from_y", "to_x", "to_y"],
            },
        },
        handler=lambda params, **kwargs: json.dumps(
            _inject(
                {
                    "op": "drag",
                    "from_x": (params or {}).get("from_x"),
                    "from_y": (params or {}).get("from_y"),
                    "to_x": (params or {}).get("to_x"),
                    "to_y": (params or {}).get("to_y"),
                    "button": (params or {}).get("button", "left"),
                },
                (params or {}).get("target"),
            )
        ),
    )
