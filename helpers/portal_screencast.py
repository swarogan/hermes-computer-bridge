#!/usr/bin/python3
"""xdg-desktop-portal ScreenCast helper.

MUST run under /usr/bin/python3 (system gi + Gst). The Hermes venv has
neither gi nor a working PipeWire binding.

Handshake (portal spec):
  CreateSession -> SelectSources -> Start -> OpenPipeWireRemote(fd)
  then one GStreamer pipewiresrc frame to PNG.

The KDE consent dialog is not bypassable. persist_mode=2 + restore_token
reduces it to once until the user revokes. That is the maximum.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_IFACE = "org.freedesktop.portal.ScreenCast"
REMOTEDESKTOP_IFACE = "org.freedesktop.portal.RemoteDesktop"
REQUEST_IFACE = "org.freedesktop.portal.Request"
SESSION_IFACE = "org.freedesktop.portal.Session"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

DEVICE_KEYBOARD = 1
DEVICE_POINTER = 2

NOTIFY_SIGNATURES = {
    "NotifyPointerMotionAbsolute": "(oa{sv}udd)",
    "NotifyPointerMotion": "(oa{sv}dd)",
    "NotifyPointerButton": "(oa{sv}iu)",
    "NotifyPointerAxis": "(oa{sv}dd)",
    "NotifyKeyboardKeycode": "(oa{sv}iu)",
    "NotifyKeyboardKeysym": "(oa{sv}iu)",
}

# Source types: Monitor=1, Window=2, Virtual=4
SOURCE_MONITOR = 1
# Cursor: Hidden=1, Embedded=2, Metadata=4
CURSOR_EMBEDDED = 2
# Persist: 0 none, 1 until app closes, 2 until revoked
PERSIST_UNTIL_REVOKED = 2

RESPONSE_SUCCESS = 0
RESPONSE_CANCELLED = 1
RESPONSE_OTHER = 2

DEFAULT_FPS = 10
MIN_FPS = 1
MAX_FPS = 30
DEFAULT_QUALITY = 75
MIN_QUALITY = 10
MAX_QUALITY = 100


def _die(code: int, msg: str, **extra: Any) -> None:
    payload = {"ok": False, "error": msg, **extra}
    print(json.dumps(payload), file=sys.stderr)
    print(json.dumps(payload))
    raise SystemExit(code)


def _require_gi():
    try:
        import gi  # type: ignore
    except ImportError as exc:
        _die(
            2,
            "gi missing — this helper must run as /usr/bin/python3, not the Hermes venv",
            interpreter=sys.executable,
            detail=str(exc),
        )
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import Gio, GLib, Gst  # type: ignore

    return gi, Gio, GLib, Gst


def _state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    path = Path(root) / "hermes-computer-bridge"
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    return path


def _token_path(name: str = "screencast-restore-token") -> Path:
    return _state_dir() / name


def load_restore_token(name: str = "screencast-restore-token") -> Optional[str]:
    p = _token_path(name)
    if not p.is_file():
        return None
    token = p.read_text(encoding="utf-8").strip()
    return token or None


def save_restore_token(token: str, name: str = "screencast-restore-token") -> None:
    p = _token_path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(token.strip() + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)


INPUT_TOKEN = "remotedesktop-restore-token"


def _sender_token(unique_name: str) -> str:
    # ':1.42' -> '1_42'
    return unique_name[1:].replace(".", "_")


def _handle_token() -> str:
    return "hdb" + uuid.uuid4().hex[:16]


class PortalClient:
    def __init__(self, Gio, GLib):
        self.Gio = Gio
        self.GLib = GLib
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.unique = self.conn.get_unique_name()
        self.sender = _sender_token(self.unique)

    def get_all(self, iface: str) -> dict:
        variant = self.conn.call_sync(
            PORTAL_BUS,
            PORTAL_PATH,
            PROPERTIES_IFACE,
            "GetAll",
            self.GLib.Variant("(s)", (iface,)),
            self.GLib.VariantType.new("(a{sv})"),
            self.Gio.DBusCallFlags.NONE,
            8000,
            None,
        )
        (props,) = variant.unpack()
        return dict(props)

    def _request_path(self, handle_token: str) -> str:
        return f"{PORTAL_PATH}/request/{self.sender}/{handle_token}"

    def call_request(
        self,
        method: str,
        parameters,
        handle_token: str,
        timeout_s: int,
        on_waiting: Optional[Callable[[], None]] = None,
        iface: str = SCREENCAST_IFACE,
    ) -> tuple[int, dict]:
        """Call a portal method that returns a Request, wait for Response."""
        Gio, GLib = self.Gio, self.GLib
        request_path = self._request_path(handle_token)
        box: dict[str, Any] = {}
        loop = GLib.MainLoop()

        def on_signal(
            _conn, _sender, _path, _iface, _signal, params, *_rest
        ):
            code, results = params.unpack()
            box["code"] = int(code)
            box["results"] = dict(results) if results is not None else {}
            loop.quit()

        sub_id = self.conn.signal_subscribe(
            PORTAL_BUS,
            REQUEST_IFACE,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            on_signal,
        )

        # Watchdog: portal dialogs block until the user answers.
        def timed_out():
            box.setdefault("code", -1)
            box.setdefault("results", {})
            box["timeout"] = True
            loop.quit()
            return False

        GLib.timeout_add_seconds(timeout_s, timed_out)
        if on_waiting:
            GLib.idle_add(lambda: (on_waiting(), False)[1])

        try:
            # call_sync may itself dispatch the Response signal (GLib
            # default context). If the handler already filled `box`,
            # running the loop would wait until the watchdog.
            self.conn.call_sync(
                PORTAL_BUS,
                PORTAL_PATH,
                iface,
                method,
                parameters,
                self.GLib.VariantType.new("(o)"),
                Gio.DBusCallFlags.NONE,
                timeout_s * 1000,
                None,
            )
            if "code" not in box:
                loop.run()
        finally:
            self.conn.signal_unsubscribe(sub_id)

        if box.get("timeout"):
            raise TimeoutError(
                f"{method} timed out after {timeout_s}s waiting for portal Response "
                f"(KDE consent dialog unanswered?)"
            )
        return box["code"], box.get("results") or {}

    def close_session(self, session_path: str) -> None:
        try:
            self.conn.call_sync(
                PORTAL_BUS,
                session_path,
                SESSION_IFACE,
                "Close",
                None,
                None,
                self.Gio.DBusCallFlags.NONE,
                3000,
                None,
            )
        except Exception:
            pass

    def notify(self, session_path: str, method: str, args: tuple) -> None:
        signature = NOTIFY_SIGNATURES[method]
        params = self.GLib.Variant(signature, (session_path, {}, *args))
        self.conn.call_sync(
            PORTAL_BUS,
            PORTAL_PATH,
            REMOTEDESKTOP_IFACE,
            method,
            params,
            None,
            self.Gio.DBusCallFlags.NONE,
            3000,
            None,
        )

    def open_pipewire_remote(self, session_path: str) -> int:
        Gio, GLib = self.Gio, self.GLib
        result, fd_list = self.conn.call_with_unix_fd_list_sync(
            PORTAL_BUS,
            PORTAL_PATH,
            SCREENCAST_IFACE,
            "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (session_path, {})),
            GLib.VariantType.new("(h)"),
            Gio.DBusCallFlags.NONE,
            15000,
            None,
            None,
        )
        (index,) = result.unpack()
        if fd_list is None:
            raise RuntimeError("OpenPipeWireRemote returned no UnixFDList")
        fd = fd_list.get(index)
        if fd < 0:
            raise RuntimeError(f"OpenPipeWireRemote fd index {index} invalid")
        return fd


def capture_one_png(
    Gst,
    fd: int,
    node_id: int,
    outfile: Path,
    timeout_s: int = 15,
    skip_frames: int = 5,
) -> None:
    """Pull one settled frame from the PipeWire node via GStreamer.

    The FIRST buffers off a fresh pipewiresrc are routinely blank: the
    compositor has not composited into the new stream yet. `pngenc
    snapshot=true` would happily encode that black rectangle and report
    success. So drop the first *skip_frames* buffers with a pad probe and
    encode the next one.
    """
    Gst.init(None)
    pw_fd = os.dup(fd)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    if outfile.exists():
        outfile.unlink()

    src = Gst.ElementFactory.make("pipewiresrc", "src")
    conv = Gst.ElementFactory.make("videoconvert", "conv")
    gate = Gst.ElementFactory.make("identity", "gate")
    enc = Gst.ElementFactory.make("pngenc", "enc")
    sink = Gst.ElementFactory.make("filesink", "sink")
    if not all((src, conv, gate, enc, sink)):
        os.close(pw_fd)
        raise RuntimeError(
            "GStreamer missing pipewiresrc/videoconvert/identity/pngenc/filesink"
        )

    src.set_property("fd", pw_fd)
    src.set_property("path", str(node_id))
    src.set_property("do-timestamp", True)
    try:
        src.set_property("always-copy", True)
    except Exception:
        pass
    enc.set_property("snapshot", True)
    sink.set_property("location", str(outfile))
    sink.set_property("sync", False)

    seen = {"n": 0}

    def drop_warmup(_pad, info):
        seen["n"] += 1
        if seen["n"] <= skip_frames:
            return Gst.PadProbeReturn.DROP
        return Gst.PadProbeReturn.OK

    gate_src = gate.get_static_pad("src")
    gate_src.add_probe(Gst.PadProbeType.BUFFER, drop_warmup)

    pipeline = Gst.Pipeline.new("hdb-capture")
    for el in (src, conv, gate, enc, sink):
        pipeline.add(el)
    if not (src.link(conv) and conv.link(gate) and gate.link(enc) and enc.link(sink)):
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("failed to link pipewiresrc ! videoconvert ! identity ! pngenc ! filesink")

    bus = pipeline.get_bus()
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("pipeline PLAYING failed")

    deadline = time.time() + timeout_s
    err_msg = None
    try:
        while time.time() < deadline:
            remaining_ns = int(max(0.05, deadline - time.time()) * 1e9)
            msg = bus.timed_pop_filtered(
                remaining_ns,
                Gst.MessageType.EOS
                | Gst.MessageType.ERROR
                | Gst.MessageType.WARNING,
            )
            if msg is None:
                if outfile.is_file() and outfile.stat().st_size > 0:
                    break
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                err_msg = f"{err.message} ({debug})"
                break
            if msg.type == Gst.MessageType.EOS:
                break
        else:
            if not (outfile.is_file() and outfile.stat().st_size > 0):
                raise TimeoutError(
                    f"no frame from pipewiresrc within {timeout_s}s "
                    f"(node {node_id}, buffers seen {seen['n']})"
                )
    finally:
        pipeline.set_state(Gst.State.NULL)

    if err_msg:
        raise RuntimeError(f"gstreamer: {err_msg}")
    if not outfile.is_file() or outfile.stat().st_size < 32:
        raise RuntimeError(f"PNG missing or empty: {outfile}")


def clamp_fps(value: int) -> int:
    return max(MIN_FPS, min(MAX_FPS, int(value)))


def clamp_quality(value: int) -> int:
    return max(MIN_QUALITY, min(MAX_QUALITY, int(value)))


def write_frame_atomically(path: Path, data: bytes) -> None:
    """Swap a whole new frame in; never truncate the one being read.

    A plain write() truncates in place, so a REST reader that opened the
    file a microsecond earlier gets a half-image. Writing a sibling temp and
    os.replace()-ing it is a rename within one directory: readers holding the
    old descriptor keep the old COMPLETE frame until they reopen.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same directory — os.replace is only atomic within one filesystem.
    tmp = path.with_name(f".{path.name}.part")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
    os.replace(tmp, path)


def is_complete_jpeg(data: bytes) -> bool:
    """Return whether an appsink buffer is a complete JPEG frame."""
    return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")


def stream_status(*, event: str, frames: int, stats: Optional[dict] = None, **extra: Any) -> dict:
    """The one place that decides whether a stream may be called healthy.

    `blank` is REPORTED, never swallowed — a sleeping monitor is legitimately
    black. But a blank frame is never `healthy`, so nothing downstream can
    dress a black rectangle up as a working live stream. Zero frames is never
    healthy either, whatever the stats claim.
    """
    stats = dict(stats or {})
    blank = bool(stats.get("blank"))
    return {
        "event": event,
        "frames": frames,
        "blank": blank,
        "healthy": frames > 0 and not blank,
        "stats": stats,
        **extra,
    }


def frame_stats(path: Path) -> dict:
    """Cheap blank-detection so callers never trust a black rectangle.

    A legitimately black screen exists (monitor asleep), so this REPORTS
    rather than fails — the decision belongs to the caller.
    """
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(str(path))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    data = pb.get_pixels()
    n = pb.get_n_channels()
    rowstride = pb.get_rowstride()
    width, height = pb.get_width(), pb.get_height()
    step_y = max(1, height // 60)
    step_x = max(1, width // 60)
    samples = []
    for y in range(0, height, step_y):
        base = y * rowstride
        for x in range(0, width, step_x):
            off = base + x * n
            samples.append((data[off], data[off + 1], data[off + 2]))
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in samples]
    mean = sum(lum) / len(lum)
    var = sum((v - mean) ** 2 for v in lum) / len(lum)
    uniq = len(set(samples))
    return {
        "available": True,
        "mean_luma": round(mean, 2),
        "variance": round(var, 2),
        "unique_colors": uniq,
        "blank": var < 1.0 or uniq < 5,
    }


def parse_streams(streams) -> list[dict]:
    """Keep metadata for EVERY stream Start returned.

    A multi-monitor session returns several streams. Hardcoding streams[0]
    silently picks whichever monitor the compositor listed first and makes
    input coordinate mapping wrong on the others. Portal stream props carry
    `size` and `position` in LOGICAL coordinates — input mapping later must
    offset by the chosen stream's position, not by the global desktop origin.
    """
    parsed: list[dict] = []
    for index, entry in enumerate(streams):
        if not isinstance(entry, (list, tuple)) or not entry:
            raise ValueError(f"unexpected stream shape at {index}: {entry!r}")
        node_id = int(entry[0])
        props = entry[1] if len(entry) > 1 else {}
        props = dict(props) if isinstance(props, dict) else {}
        size = props.get("size")
        position = props.get("position")
        parsed.append(
            {
                "index": index,
                "node_id": node_id,
                "size": _jsonable(size),
                "position": _jsonable(position),
                "source_type": _jsonable(props.get("source_type")),
                "id": _jsonable(props.get("id")),
                "mapping_id": _jsonable(props.get("mapping_id")),
                "props": _jsonable(props),
            }
        )
    return parsed


def select_stream(
    parsed: list[dict],
    *,
    stream_index: Optional[int] = None,
    node_id: Optional[int] = None,
) -> dict:
    """Explicit monitor choice; index 0 is only the documented default."""
    if node_id is not None:
        for s in parsed:
            if s["node_id"] == node_id:
                return s
        raise ValueError(
            f"node_id {node_id} not among streams: {[s['node_id'] for s in parsed]}"
        )
    if stream_index is not None:
        if not 0 <= stream_index < len(parsed):
            raise ValueError(
                f"stream index {stream_index} out of range (0..{len(parsed) - 1})"
            )
        return parsed[stream_index]
    return parsed[0]


def probe() -> dict:
    gi, Gio, GLib, Gst = _require_gi()
    client = PortalClient(Gio, GLib)
    sc = client.get_all(SCREENCAST_IFACE)
    try:
        rd = client.get_all("org.freedesktop.portal.RemoteDesktop")
    except Exception as exc:
        rd = {"error": str(exc)}
    return {
        "ok": True,
        "interpreter": sys.executable,
        "gi": getattr(gi, "__file__", None),
        "screencast": sc,
        "remote_desktop": rd,
        "restore_token_present": load_restore_token() is not None,
        "session": {
            "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE"),
            "XDG_CURRENT_DESKTOP": os.environ.get("XDG_CURRENT_DESKTOP"),
            "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
        },
    }


class PortalSession:
    """One completed handshake: live session, chosen stream, open PipeWire fd.

    Both `spike` (one PNG) and `stream` (long-lived pipeline) need exactly
    this and nothing more. Sharing it is what keeps the one path proven on a
    real desktop from drifting away from the one that is not.
    """

    def __init__(self, client, session_path, parsed, chosen, fd, restore_saved, log):
        self.client = client
        self.session_path = session_path
        self.parsed = parsed
        self.chosen = chosen
        self.fd = fd
        self.restore_saved = restore_saved
        self.log = log

    def close(self) -> None:
        """Idempotent: teardown runs from both the happy path and a signal."""
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.session_path is not None:
            self.client.close_session(self.session_path)
            self.session_path = None


def portal_handshake(
    Gio,
    GLib,
    *,
    persist_mode: int,
    timeout_s: int,
    stream_index: Optional[int] = None,
    node_id_arg: Optional[int] = None,
    enable_input: bool = False,
) -> PortalSession:
    """CreateSession -> SelectSources -> Start -> OpenPipeWireRemote, once."""
    portal_iface = REMOTEDESKTOP_IFACE if enable_input else SCREENCAST_IFACE
    GLib.set_prgname("hermes-computer-bridge")
    try:
        GLib.set_application_name("Hermes Desktop Bridge")
    except Exception:
        pass

    client = PortalClient(Gio, GLib)
    log: list[str] = []

    def step(name: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {name}"
        log.append(line)
        print(line, file=sys.stderr, flush=True)

    session_token = _handle_token()
    create_token = _handle_token()
    session_path = f"{PORTAL_PATH}/session/{client.sender}/{session_token}"

    step("CreateSession")
    create_opts = {
        "handle_token": GLib.Variant("s", create_token),
        "session_handle_token": GLib.Variant("s", session_token),
    }
    code, results = client.call_request(
        "CreateSession",
        GLib.Variant("(a{sv})", (create_opts,)),
        create_token,
        timeout_s=min(30, timeout_s),
        iface=portal_iface,
    )
    if code != RESPONSE_SUCCESS:
        _die(3, f"CreateSession response={code}", log=log, results=_jsonable(results))
    if "session_handle" in results:
        session_path = str(results["session_handle"])
    step(f"session={session_path}")

    if enable_input:
        devices_restore = load_restore_token(INPUT_TOKEN)
        source_restore = None
        source_persist = 0
    else:
        devices_restore = None
        source_restore = load_restore_token()
        source_persist = persist_mode

    if enable_input:
        devices_token = _handle_token()
        devices_opts = {
            "handle_token": GLib.Variant("s", devices_token),
            "types": GLib.Variant("u", DEVICE_KEYBOARD | DEVICE_POINTER),
            "persist_mode": GLib.Variant("u", persist_mode),
        }
        if devices_restore:
            devices_opts["restore_token"] = GLib.Variant("s", devices_restore)
        step("SelectDevices (keyboard + pointer)")
        try:
            code, results = client.call_request(
                "SelectDevices",
                GLib.Variant("(oa{sv})", (session_path, devices_opts)),
                devices_token,
                timeout_s=timeout_s,
                iface=REMOTEDESKTOP_IFACE,
            )
        except TimeoutError as exc:
            client.close_session(session_path)
            _die(4, str(exc), log=log, hint="unanswered KDE RemoteDesktop dialog")
        if code == RESPONSE_CANCELLED:
            client.close_session(session_path)
            _die(5, "user cancelled RemoteDesktop consent", log=log)
        if code != RESPONSE_SUCCESS:
            client.close_session(session_path)
            _die(6, f"SelectDevices response={code}", log=log, results=_jsonable(results))
    select_token = _handle_token()
    select_opts = {
        "handle_token": GLib.Variant("s", select_token),
        "types": GLib.Variant("u", SOURCE_MONITOR),
        "multiple": GLib.Variant("b", False),
        "cursor_mode": GLib.Variant("u", CURSOR_EMBEDDED),
        "persist_mode": GLib.Variant("u", source_persist),
    }
    if source_restore:
        select_opts["restore_token"] = GLib.Variant("s", source_restore)
        step("SelectSources (restore_token present — dialog should be skipped or reduced)")
    else:
        step(
            "SelectSources — KDE consent dialog MUST appear. "
            "Pick a monitor and Allow. This is not bypassable."
        )

    try:
        code, results = client.call_request(
            "SelectSources",
            GLib.Variant("(oa{sv})", (session_path, select_opts)),
            select_token,
            timeout_s=timeout_s,
            on_waiting=lambda: print(
                "WAITING_FOR_PORTAL_CONSENT", file=sys.stderr, flush=True
            ),
        )
    except TimeoutError as exc:
        client.close_session(session_path)
        _die(4, str(exc), log=log, hint="unanswered KDE ScreenCast dialog")

    if code == RESPONSE_CANCELLED:
        client.close_session(session_path)
        _die(5, "user cancelled ScreenCast consent", log=log)
    if code != RESPONSE_SUCCESS:
        client.close_session(session_path)
        _die(6, f"SelectSources response={code}", log=log, results=_jsonable(results))

    start_token = _handle_token()
    start_opts = {"handle_token": GLib.Variant("s", start_token)}
    step("Start")
    try:
        code, results = client.call_request(
            "Start",
            GLib.Variant("(osa{sv})", (session_path, "", start_opts)),
            start_token,
            timeout_s=timeout_s,
            iface=portal_iface,
        )
    except TimeoutError as exc:
        client.close_session(session_path)
        _die(4, str(exc), log=log)

    if code == RESPONSE_CANCELLED:
        client.close_session(session_path)
        _die(5, "user cancelled ScreenCast start", log=log)
    if code != RESPONSE_SUCCESS:
        client.close_session(session_path)
        _die(6, f"Start response={code}", log=log, results=_jsonable(results))

    streams = results.get("streams") or []
    restore_token = results.get("restore_token")
    if restore_token:
        save_restore_token(
            str(restore_token), INPUT_TOKEN if enable_input else "screencast-restore-token"
        )
        step("saved restore_token")

    if not streams:
        client.close_session(session_path)
        _die(7, "Start returned no streams", log=log, results=_jsonable(results))

    parsed = parse_streams(streams)
    for s in parsed:
        step(
            f"stream node_id={s['node_id']} size={s['size']} "
            f"position={s['position']} source_type={s['source_type']}"
        )

    chosen = select_stream(parsed, stream_index=stream_index, node_id=node_id_arg)
    step(f"selected node_id={chosen['node_id']} (of {len(parsed)} stream(s))")

    step("OpenPipeWireRemote")
    try:
        fd = client.open_pipewire_remote(session_path)
    except Exception as exc:
        client.close_session(session_path)
        _die(8, f"OpenPipeWireRemote failed: {exc}", log=log)

    step(f"got pw fd={fd}")
    return PortalSession(
        client=client,
        session_path=session_path,
        parsed=parsed,
        chosen=chosen,
        fd=fd,
        restore_saved=bool(restore_token) or load_restore_token() is not None,
        log=log,
    )


def spike(
    outfile: Path,
    persist_mode: int,
    timeout_s: int,
    stream_index: Optional[int] = None,
    node_id_arg: Optional[int] = None,
) -> dict:
    gi, Gio, GLib, Gst = _require_gi()
    del gi
    session = portal_handshake(
        Gio,
        GLib,
        persist_mode=persist_mode,
        timeout_s=timeout_s,
        stream_index=stream_index,
        node_id_arg=node_id_arg,
    )
    chosen = session.chosen
    node_id = chosen["node_id"]
    size = chosen["size"]
    parsed = session.parsed
    log = session.log

    log.append(f"capturing one PNG -> {outfile}")
    try:
        capture_one_png(Gst, session.fd, node_id, outfile)
    finally:
        session.close()

    st = outfile.stat()
    stats = frame_stats(outfile)
    if stats.get("blank"):
        log.append(f"WARNING blank frame: {stats}")
        print(f"WARNING blank frame: {stats}", file=sys.stderr, flush=True)
    payload = {
        "ok": True,
        "path": str(outfile.resolve()),
        "bytes": st.st_size,
        "node_id": node_id,
        "size": _jsonable(size),
        "stats": stats,
        "selected_stream": _jsonable(chosen),
        "streams": _jsonable(parsed),
        "stream_count": len(parsed),
        "restore_token_saved": session.restore_saved,
        "log": log,
    }
    print(json.dumps(payload), flush=True)
    return payload


def build_live_pipeline(Gst, *, pw_fd: int, node_id: int, fps: int, quality: int, on_sample):
    """The live graph: pipewiresrc ! videoconvert ! videorate ! jpegenc ! appsink.

    Split out of `stream_forever` so it can be built — and therefore checked —
    without a portal session. Everything that silently breaks here (a caps
    typo, a property this GStreamer does not have, pads that will not link)
    is caught by building the graph, which needs no consent dialog.
    """
    src = Gst.ElementFactory.make("pipewiresrc", "src")
    conv = Gst.ElementFactory.make("videoconvert", "conv")
    rate = Gst.ElementFactory.make("videorate", "rate")
    caps = Gst.ElementFactory.make("capsfilter", "caps")
    enc = Gst.ElementFactory.make("jpegenc", "enc")
    sink = Gst.ElementFactory.make("appsink", "sink")
    elements = {"src": src, "conv": conv, "rate": rate, "caps": caps, "enc": enc, "sink": sink}
    missing = [name for name, el in elements.items() if el is None]
    if missing:
        raise RuntimeError(f"GStreamer is missing: {', '.join(missing)}")

    src.set_property("fd", pw_fd)
    src.set_property("path", str(node_id))
    src.set_property("do-timestamp", True)
    try:
        src.set_property("always-copy", True)
    except Exception:
        pass
    # videorate + a framerate cap is what makes this a *paced* stream instead
    # of a firehose that pegs a core re-encoding every compositor repaint.
    caps.set_property("caps", Gst.Caps.from_string(f"video/x-raw,framerate={fps}/1"))
    enc.set_property("quality", quality)
    sink.set_property("emit-signals", True)
    sink.set_property("sync", False)
    # Never queue stale frames: a live view wants the newest, not a backlog.
    sink.set_property("max-buffers", 1)
    sink.set_property("drop", True)
    sink.connect("new-sample", on_sample)

    pipeline = Gst.Pipeline.new("hdb-live")
    for el in (src, conv, rate, caps, enc, sink):
        pipeline.add(el)
    if not (
        src.link(conv)
        and conv.link(rate)
        and rate.link(caps)
        and caps.link(enc)
        and enc.link(sink)
    ):
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("failed to link the live pipeline")
    return pipeline, elements


def stream_forever(
    outfile: Path,
    *,
    persist_mode: int,
    timeout_s: int,
    fps: int = DEFAULT_FPS,
    quality: int = DEFAULT_QUALITY,
    stream_index: Optional[int] = None,
    node_id_arg: Optional[int] = None,
    status_interval: float = 2.0,
    skip_frames: int = 5,
    enable_input: bool = False,
) -> int:
    """ONE portal session, ONE PipeWire fd, ONE pipeline, frames until killed.

    This is the difference between a prosthesis and a stream: the consent,
    the fd and the pipeline are paid for once, then every decoded buffer is
    swapped into `outfile` atomically. The parent learns the stream is LIVE
    from a JSON line on the first real frame — never from a sleep().
    """
    import signal

    gi, Gio, GLib, Gst = _require_gi()
    del gi
    fps = clamp_fps(fps)
    quality = clamp_quality(quality)
    outfile = Path(outfile)

    session = portal_handshake(
        Gio,
        GLib,
        persist_mode=persist_mode,
        timeout_s=timeout_s,
        stream_index=stream_index,
        node_id_arg=node_id_arg,
        enable_input=enable_input,
    )
    chosen = session.chosen
    node_id = chosen["node_id"]
    meta = {
        "output": str(outfile.resolve()),
        "fps": fps,
        "quality": quality,
        "node_id": node_id,
        "selected_stream": _jsonable(chosen),
        "streams": _jsonable(session.parsed),
        "stream_count": len(session.parsed),
        "restore_token_saved": session.restore_saved,
    }

    def emit(payload: dict) -> None:
        print(json.dumps(payload), flush=True)

    Gst.init(None)
    pw_fd = os.dup(session.fd)
    state = {"seen": 0, "frames": 0, "announced": False, "error": None}

    def on_sample(appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = bytes(mapinfo.data)
        finally:
            buf.unmap(mapinfo)

        state["seen"] += 1
        # The first buffers off a fresh pipewiresrc are routinely black: the
        # compositor has not composited into the new stream yet. Same warm-up
        # skip capture_one_png relies on — dropping it here would publish a
        # black rectangle as the first "live" frame.
        if state["seen"] <= skip_frames:
            return Gst.FlowReturn.OK

        # appsink normally yields one complete jpegenc output buffer, but a
        # transient short/partial buffer must never replace the last good
        # frame: consumers treat each atomic replacement as publishable.
        if not is_complete_jpeg(data):
            return Gst.FlowReturn.OK
        write_frame_atomically(outfile, data)
        state["frames"] += 1
        if not state["announced"]:
            state["announced"] = True
            emit(
                stream_status(
                    event="live",
                    frames=state["frames"],
                    stats=frame_stats(outfile),
                    **meta,
                )
            )
        return Gst.FlowReturn.OK

    try:
        pipeline, _elements = build_live_pipeline(
            Gst,
            pw_fd=pw_fd,
            node_id=node_id,
            fps=fps,
            quality=quality,
            on_sample=on_sample,
        )
    except RuntimeError as exc:
        os.close(pw_fd)
        session.close()
        _die(9, str(exc))

    loop = GLib.MainLoop()

    def shutdown(*_args) -> bool:
        loop.quit()
        return False

    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, shutdown)

    def on_bus(_bus, msg) -> bool:
        if msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            state["error"] = f"{err.message} ({debug})"
            loop.quit()
        elif msg.type == Gst.MessageType.EOS:
            state["error"] = state["error"] or "pipewiresrc reached EOS"
            loop.quit()
        return True

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_bus)

    # A stalled stream is indistinguishable from a live one unless the frame
    # counter keeps moving, so keep reporting rather than going silent.
    def tick() -> bool:
        stats = frame_stats(outfile) if outfile.is_file() else {}
        emit(
            stream_status(
                event="status",
                frames=state["frames"],
                stats=stats,
                buffers_seen=state["seen"],
                **meta,
            )
        )
        return True

    GLib.timeout_add(max(250, int(status_interval * 1000)), tick)

    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        os.close(pw_fd)
        session.close()
        _die(9, "live pipeline PLAYING failed")

    if enable_input:
        import threading as _threading

        repo_root = str(Path(__file__).resolve().parent.parent)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from hermes_computer_bridge.input_protocol import input_calls as _input_calls

        def _dispatch(cmd):
            try:
                for method, args in _input_calls(cmd, node_id):
                    session.client.notify(session.session_path, method, args)
            except Exception as exc:
                emit(
                    stream_status(
                        event="input_error",
                        frames=state["frames"],
                        stats={},
                        error=str(exc),
                        **meta,
                    )
                )
            return False

        def _read_input():
            for raw in sys.stdin:
                text = raw.strip()
                if not text:
                    continue
                try:
                    cmd = json.loads(text)
                except Exception:
                    continue
                GLib.idle_add(_dispatch, cmd)

        _threading.Thread(target=_read_input, daemon=True).start()
        emit(stream_status(event="input_ready", frames=state["frames"], stats={}, **meta))

    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        try:
            os.close(pw_fd)
        except OSError:
            pass
        session.close()

    if state["error"]:
        emit(
            stream_status(
                event="error",
                frames=state["frames"],
                stats={},
                error=state["error"],
                **meta,
            )
        )
        return 9
    emit(stream_status(event="stopped", frames=state["frames"], stats={}, **meta))
    return 0


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="read portal properties; no consent dialog")
    sp = sub.add_parser("spike", help="full handshake + one PNG")
    sp.add_argument(
        "-o",
        "--output",
        default="",
        help="PNG path (default: ./evidence/portal-frame.png)",
    )
    sp.add_argument("--persist-mode", type=int, default=PERSIST_UNTIL_REVOKED)
    sp.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="seconds to wait for KDE consent (SelectSources/Start)",
    )
    sp.add_argument(
        "--stream-index",
        type=int,
        default=None,
        help="which returned stream (monitor) to capture; default 0",
    )
    sp.add_argument(
        "--node-id",
        type=int,
        default=None,
        help="capture the stream with this PipeWire node id (overrides --stream-index)",
    )
    st = sub.add_parser(
        "stream", help="one portal session, long-lived pipeline, continuous JPEG"
    )
    st.add_argument(
        "-o",
        "--output",
        default="",
        help="JPEG path rewritten atomically per frame "
        "(default: ./evidence/live-frame.jpg)",
    )
    st.add_argument("--fps", type=int, default=DEFAULT_FPS, help="target frames/sec")
    st.add_argument(
        "--quality", type=int, default=DEFAULT_QUALITY, help="jpegenc quality 1..100"
    )
    st.add_argument("--persist-mode", type=int, default=PERSIST_UNTIL_REVOKED)
    st.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="seconds to wait for KDE consent (SelectSources/Start)",
    )
    st.add_argument(
        "--stream-index",
        type=int,
        default=None,
        help="which returned stream (monitor) to stream; default 0",
    )
    st.add_argument(
        "--node-id",
        type=int,
        default=None,
        help="stream the PipeWire node with this id (overrides --stream-index)",
    )
    st.add_argument(
        "--status-interval",
        type=float,
        default=2.0,
        help="seconds between periodic JSON status lines",
    )
    st.add_argument(
        "--input",
        action="store_true",
        help="combined RemoteDesktop session; read JSON input commands on stdin",
    )
    args = parser.parse_args(argv)

    try:
        if args.cmd == "probe":
            print(json.dumps(probe(), indent=2))
            return 0
        if args.cmd == "spike":
            out = Path(args.output) if args.output else Path("evidence/portal-frame.png")
            if not out.is_absolute():
                # relative to repo root if we can see it, else cwd
                here = Path(__file__).resolve()
                repo = here.parent.parent
                out = repo / out
            spike(
                out,
                persist_mode=args.persist_mode,
                timeout_s=args.timeout,
                stream_index=args.stream_index,
                node_id_arg=args.node_id,
            )
            return 0
        if args.cmd == "stream":
            out = Path(args.output) if args.output else Path("evidence/live-frame.jpg")
            if not out.is_absolute():
                out = Path(__file__).resolve().parent.parent / out
            return stream_forever(
                out,
                persist_mode=args.persist_mode,
                timeout_s=args.timeout,
                fps=args.fps,
                quality=args.quality,
                stream_index=args.stream_index,
                node_id_arg=args.node_id,
                status_interval=args.status_interval,
                enable_input=args.input,
            )
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        _die(1, traceback.format_exc())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
