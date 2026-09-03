"""Hermes plugin: agent tools over the capture and input ladders.

Capture runs through the provider ABC. click/type/key inject through the live
portal RemoteDesktop stream when one is up, else the input-service ladder
(wlrctl / xdotool), else they report capability-missing honestly.
"""

from __future__ import annotations

import json
import logging
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
from hermes_computer_bridge.targets import (  # noqa: E402
    label_for,
    list_targets,
    parse_target,
    proxmox_config,
)
from hermes_computer_bridge.agent_vnc import AgentVnc  # noqa: E402

EVIDENCE_DIR = PLUGIN_DIR / "evidence"
DEFAULT_FRAME = EVIDENCE_DIR / "live-frame.png"
# The panel's live stream rewrites this one path per frame (dashboard/plugin_api
# LIVE_STREAM_FRAME). When it is streaming the target the agent asked about, the
# newest pixels are already on disk and a fresh RFB round trip buys nothing.
LIVE_STREAM_FRAME = EVIDENCE_DIR / "live-frame.jpg"
STREAM_FRAME_MAX_AGE_S = 2.0


def _profile_name() -> str:
    """Which bot this process serves — the key `bindings.json` is stored under."""
    import os

    name = os.environ.get("HERMES_PROFILE")
    if name:
        return name
    argv = sys.argv
    if "--profile" in argv:
        index = argv.index("--profile")
        if index + 1 < len(argv):
            return argv[index + 1]
    return "default"


def _selected_target() -> str | None:
    """The machine the human picked for THIS bot, across processes.

    `live_registry` only knows what happened inside one process: the panel runs
    against the default profile's backend, so a bot like `tom` has its own
    `serve` process where the registry is empty and every tool would silently
    fall back to `local`. `bindings.json` is the shared record the panel writes
    per profile, so it is the authority; the registry is just the faster answer
    when this process is the one hosting the panel.
    """
    live = live_registry.get_panel_target()
    if live:
        return live
    try:
        data = json.loads(_bindings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(_profile_name()) or data.get("default")
    return str(value) if value else None


def _bindings_file() -> Path:
    from hermes_computer_bridge.targets import config_file

    return config_file().parent / "bindings.json"


_LAST_INPUT_AT: float = 0.0


def _note_input_sent() -> None:
    """Remember when we last changed the guest, so no stale frame can deny it."""
    import time

    global _LAST_INPUT_AT
    _LAST_INPUT_AT = time.time()


def _stream_frame_for(
    target_id: str, now: float | None = None, max_age_s: float = STREAM_FRAME_MAX_AGE_S
) -> tuple[Path, float] | None:
    """The live stream's newest frame for `target_id`, with its age in seconds.

    Freshness is the whole test, and it is deliberately the only one. The file
    is written by whichever process hosts the panel, so this process cannot ask
    its own registry whether a stream is running. It does not need to: a frame
    younger than the cap proves one is. Switch the panel off and the file simply
    stops ageing forward — within `max_age_s` this goes quiet on its own.
    """
    import time

    if _selected_target() != target_id:
        return None
    try:
        mtime = LIVE_STREAM_FRAME.stat().st_mtime
    except OSError:
        return None
    # A frame taken BEFORE our last keystroke or click cannot show its effect.
    # Serving one made the agent read "input succeeded but the text is missing"
    # and made the picture appear to travel backwards in time, because the next
    # look might fall back to a fresh capture instead.
    if mtime <= _LAST_INPUT_AT:
        return None
    age = (time.time() if now is None else now) - mtime
    if age < 0 or age > max_age_s:
        return None
    return LIVE_STREAM_FRAME, age


_LABEL_LOOKUP_DONE = False


def _label_lookup_once(target_id: str) -> str | None:
    """Populate the label cache from one `list_targets()` call, at most once.

    This is the only network touch on the per-turn path, so it must not repeat:
    a Proxmox that is slow or down would otherwise tax every turn forever.
    """
    global _LABEL_LOOKUP_DONE

    if _LABEL_LOOKUP_DONE:
        return None
    _LABEL_LOOKUP_DONE = True
    try:
        list_targets()
    except Exception:  # noqa: BLE001
        return None
    return label_for(target_id)


def _panel_context() -> str | None:
    """One line telling the bot which machine it drives and where to look.

    Injected per turn via the pre_llm_call hook. The tools are deferred, so
    without this the model has to guess that a remote machine is even reachable
    — it went looking for SSH instead. Deliberately NOT the image itself: the
    path costs a few tokens, an inlined screenshot costs ~200k characters every
    time the model merely wants to check whether anything changed.
    """
    target = _selected_target()
    if not target:
        return None
    # Naming the machine matters: the human says "omarchy", and 'vm:113' alone
    # leaves the model to guess they are the same thing. The label cache is
    # per-process and only the panel's process calls /targets, so a bot's own
    # process starts empty and has to fill it once — never per turn, and never
    # again if the lookup comes back without a name.
    label = label_for(target) or _label_lookup_once(target)
    named = f"{label} (target id: {target})" if label else target
    where = ""
    frame = _stream_frame_for(target)
    if frame is not None:
        where = (
            f" A live screenshot of it is at {frame[0]}, refreshed continuously —"
            " read it with vision_analyze when you need to see the screen."
        )
    return (
        f"Computer: this bot can view and control {named}, the machine currently "
        f"selected in the Computer panel.{where} Act on it with the "
        "computer_bridge_* tools (screenshot/click/type/key/scroll); omit their "
        "'target' argument and they use this machine."
    )

_TARGET_PROP = {
    "type": "string",
    "description": (
        "Which computer to act on: 'local', 'vm:<id>' (e.g. 'vm:112'), or "
        "'vnc:<name>'. Omit to use whatever the human has selected in the "
        "Computer panel (see computer_bridge_targets for the list and the "
        "current selection)."
    ),
}

_log = logging.getLogger("hermes_computer_bridge")

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
    if target is None:
        target = _selected_target()
    try:
        info = parse_target(target)
    except ValueError as exc:
        return {"ok": False, "kind": "bad_request", "error": str(exc)}
    live_registry.set_agent_target(info["id"])

    if info["kind"] in ("vm", "vnc"):
        if _vnc().send(info, cmd):
            _note_input_sent()
            return {"ok": True, "rung": info["id"]}
        return {"ok": False, "kind": "transient", "error": "remote input not delivered"}

    stream = live_registry.get_current()
    if stream is not None and stream.is_running():
        if stream.send(cmd):
            _note_input_sent()
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


def _paste(params: dict[str, Any] | None) -> dict[str, Any]:
    """Deliver text through the guest's clipboard instead of the keyboard.

    One RFB packet for the whole string, then Shift+Insert. Nothing to drop
    mid-stream and no dependence on the guest's keyboard layout, which is what
    made typed shell commands arrive mangled. Remote only: the local desktop
    has no clipboard channel here.
    """
    params = params or {}
    text = str(params.get("text") or "")
    if not text:
        return {"ok": False, "kind": "bad_request", "error": "nothing to paste"}
    target = params.get("target")
    if target is None:
        target = _selected_target()
    try:
        info = parse_target(target)
    except ValueError as exc:
        return {"ok": False, "kind": "bad_request", "error": str(exc)}
    if info["kind"] not in ("vm", "vnc"):
        return {
            "ok": False,
            "kind": "missing",
            "error": "paste needs a remote target; type on the local desktop",
        }
    live_registry.set_agent_target(info["id"])
    if not _vnc().paste(info, text):
        return {"ok": False, "kind": "transient", "error": "paste not delivered"}
    _note_input_sent()
    return {"ok": True, "rung": info["id"], "pasted_chars": len(text)}


def _machine_name() -> str | None:
    """Human name of the machine this bot drives, e.g. 'VM 113 (omarchy)'."""
    target = _selected_target()
    if not target:
        return None
    label = label_for(target) or _label_lookup_once(target)
    return f"{label} (target id: {target})" if label else target


def _system_prompt_section(session_info: Any = None) -> str:
    """Standing brief about this bot's remote machine, for the system prompt.

    A per-turn note (see the pre_llm_call hook) sits in the user message, where
    it competes with the task text. This lands in the system prompt instead, at
    the same level as the tool policy the model actually plans from.

    The warning about computer_use is the point of the whole section. That tool
    is always visible, its name reads like the obvious answer to "use the
    computer", and it drives THIS desktop through cua-driver. A model told to
    work on a remote VM reached for it repeatedly — and once it stopped erroring
    out, it started succeeding against the wrong machine.
    """
    named = _machine_name()
    if not named:
        return ""
    lines = [
        "## Remote computer",
        "",
        f"This bot can view and control {named} — a separate machine reached "
        "over VNC, chosen by the human in the Computer panel. Drive it with the "
        "computer_bridge_* tools; omit their 'target' argument and they act on "
        "that machine.",
        "",
        "- computer_bridge_paste — shell commands and long text. One packet, so "
        "nothing is dropped; prefer it over typing.",
        "- computer_bridge_screenshot — returns a FILE PATH, not pixels. Open it "
        "with vision_analyze when you need to see the screen.",
        "- computer_bridge_click / _type / _key / _scroll — GUI interaction.",
        "- computer_bridge_targets — list the machines and switch between them.",
        "",
        "Do NOT use computer_use for this machine. It drives the LOCAL desktop "
        "through cua-driver, so its clicks and keystrokes land on the human's "
        "own computer, not on the VM — silently, and with no error to warn you.",
    ]
    return "\n".join(lines)


def _capture(params: dict[str, Any] | None) -> str:
    params = params or {}
    target = params.get("target")
    if target is None:
        target = _selected_target()
    try:
        info = parse_target(target)
    except ValueError as exc:
        return json.dumps({"ok": False, "kind": "bad_request", "error": str(exc)})
    live_registry.set_agent_target(info["id"])

    if info["kind"] in ("vm", "vnc"):
        # A caller that named an output file wants a file written there, so the
        # stream shortcut only applies to the default path.
        if not params.get("output"):
            fresh = _stream_frame_for(info["id"])
            if fresh is not None:
                path, age = fresh
                return json.dumps(
                    {
                        "ok": True,
                        "output": str(path),
                        "target": info["id"],
                        "frame_age_ms": int(age * 1000),
                        "source": "live-stream",
                        "view_with": "vision_analyze",
                    }
                )
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
                "source": "capture",
                "view_with": "vision_analyze",
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


def _pre_llm_call(**kwargs: Any) -> dict[str, Any] | None:
    """Per-turn note naming the machine the human currently has selected."""
    line = _panel_context()
    _log.info(
        "computer-bridge pre_llm_call: target=%r context=%s",
        _selected_target(),
        "yes" if line else "no",
    )
    return {"context": line} if line else None


def register(ctx) -> None:  # noqa: ANN001
    ctx.register_tool(
        name="computer_bridge_screenshot",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_screenshot",
            # The deferred-tool catalog shows only the first ~60 characters, so
            # the opening clause has to say what this is FOR — driving a browser
            # or app on another machine — not how it is built. An opener about
            # the capability ladder read as "local desktop only" and sent the
            # model looking for SSH instead.
            "description": (
                "See a remote VM's screen or this desktop — use it to drive a "
                "browser or any app on another computer. Returns the image's "
                "FILE PATH, not the pixels: pass that path to vision_analyze "
                "when you actually need to look. Serves the panel's live "
                "frame when one is fresh, else captures one frame "
                "through the capability ladder (portal ScreenCast first, then "
                "wlr/X11/remote) and returns the PNG path, the stream used, "
                "every stream offered, and how frame pixels map onto real "
                "outputs."
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
                "Remote VMs and this desktop: list every computer this bot can "
                "view and control, including running Proxmox VMs reached over "
                "VNC. Use the returned 'id' (e.g. 'vm:112') as the 'target' "
                "argument on the other tools. Start here when the task names a "
                "machine other than this one."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda params, **kwargs: json.dumps(
            {"targets": list_targets(), "current": _selected_target() or "local"}
        ),
    )

    ctx.register_tool(
        name="computer_bridge_status",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_status",
            "description": (
                "Check whether a remote VM or this desktop can be viewed right "
                "now: reports which capture rung would serve and what each rung "
                "said. Does not open a session or prompt for consent."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        handler=lambda params, **kwargs: json.dumps(
            {**_service.probe(), "current_target": _selected_target() or "local"}
        ),
    )

    ctx.register_tool(
        name="computer_bridge_click",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_click",
            "description": (
                "Click on a remote VM's screen or this desktop, at a pixel from "
                "computer_bridge_screenshot (see it for the coordinate space). "
                "Needs a live portal RemoteDesktop stream, else an input rung."
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
            "description": (
                "Type text on a remote VM or this desktop, into whatever window "
                "has focus there."
            ),
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
                "Press a key or chord on a remote VM or this desktop, e.g. "
                "key='Return', or key='c' with mods=['ctrl']."
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
            "description": (
                "Move the pointer on a remote VM or this desktop, to a "
                "captured-frame pixel, without clicking."
            ),
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
            "description": (
                "Scroll the wheel on a remote VM or this desktop. Positive dy "
                "scrolls down, negative up."
            ),
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
                "Drag on a remote VM or this desktop: press at one captured-frame "
                "pixel, move to another, and release. Coordinates are frame pixels."
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

    ctx.register_tool(
        name="computer_bridge_paste",
        toolset="computer_bridge",
        schema={
            "name": "computer_bridge_paste",
            "description": (
                "Send text to a remote VM in one shot via its clipboard, then "
                "Shift+Insert — use this for shell commands and any long text. "
                "Typing key-by-key is lossy on a busy guest and depends on its "
                "keyboard layout; this does not. Non-Latin-1 characters are "
                "replaced, so use computer_bridge_type for accented text. "
                "Restores the guest's previous clipboard afterwards."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to paste."},
                    "target": _TARGET_PROP,
                },
                "required": ["text"],
            },
        },
        handler=lambda params, **kwargs: json.dumps(_paste(params)),
    )

    # Tools are deferred: the model sees names in a catalog, not schemas. The
    # per-turn line from _panel_context is what tells it a remote machine is
    # already selected and reachable — without it the model reached for
    # computer_use (which fails on Wayland here) or hunted for SSH.
    ctx.register_hook("pre_llm_call", _pre_llm_call)

    # The system prompt is where the model reads its tool policy, so the
    # standing brief goes there; the hook above only refreshes which machine is
    # selected. Registering a section is best-effort: an older host without the
    # API must still get a working plugin.
    try:
        ctx.register_system_prompt_section(
            "computer-bridge.remote-machine",
            lambda session_info=None: _system_prompt_section(session_info),
        )
    except Exception as exc:  # noqa: BLE001
        _log.info("system prompt section unavailable: %r", exc)
