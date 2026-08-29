#!/usr/bin/env python3
"""Real E2E: mount the router in a live HTTP server and drive it over the wire.

Not a mock. Uvicorn serves the same APIRouter the gateway mounts, at the same
`/api/plugins/<name>` prefix, and every assertion below is a real HTTP
response. Capture is only attempted with --capture (it opens a portal
session); otherwise this exercises status/frame-data/map against whatever
frame already exists.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "hermes-computer-bridge"
PREFIX = f"/api/plugins/{NAME}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_router():
    api = REPO / "dashboard" / "plugin_api.py"
    module_name = f"hermes_dashboard_plugin_{NAME}"
    spec = importlib.util.spec_from_file_location(module_name, api)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod.router


def get(url: str, timeout: float = 30.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def post(url: str, payload: dict, timeout: float = 240.0):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", help="also POST /capture (opens a portal session)")
    args = parser.parse_args()

    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(load_router(), prefix=PREFIX)

    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}{PREFIX}"
    deadline = time.time() + 20
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        print("FAIL: server did not start")
        return 1
    print(f"server listening on 127.0.0.1:{port}")

    failures = []

    try:
        status_code, status = get(f"{base}/status")
        print(f"\nGET /status -> {status_code}")
        print("  portal_ok      :", status.get("ok"))
        print("  frame_present  :", status.get("frame_present"))
        print("  frame_name     :", status.get("frame_name"))
        print("  frame_version  :", status.get("frame_version"))
        print("  outputs        :", len(status.get("outputs") or []))
        if status_code != 200:
            failures.append("status != 200")

        if args.capture:
            print("\nPOST /capture (portal session; consent may be required)…")
            cap_code, cap = post(f"{base}/capture", {"output": "live-frame.png", "timeout_s": 180})
            print(f"POST /capture -> {cap_code}")
            print("  rung   :", cap.get("rung"))
            print("  blank  :", cap.get("stats", {}).get("blank"))
            print("  var    :", cap.get("stats", {}).get("variance"))
            print("  size   :", cap.get("width"), "x", cap.get("height"))
            if cap_code != 200:
                failures.append("capture != 200")
            elif cap.get("stats", {}).get("blank"):
                failures.append("captured frame is BLANK")
            status_code, status = get(f"{base}/status")

        version = status.get("frame_version")
        if not version:
            failures.append("no frame available to serve")
        else:
            fd_code, fd = get(f"{base}/frame-data?version={version}", timeout=60)
            print(f"\nGET /frame-data -> {fd_code}")
            url = fd.get("data_url", "")
            raw = base64.b64decode(url.split(",", 1)[1]) if "," in url else b""
            print("  media_type   :", fd.get("media_type"))
            print("  data_url head:", url[:44])
            print("  decoded bytes:", len(raw))
            print("  png magic ok :", raw[:8] == b"\x89PNG\r\n\x1a\n")
            on_disk = Path(fd["frame_path"]).read_bytes()
            print("  matches disk :", raw == on_disk)
            if fd_code != 200 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw != on_disk:
                failures.append("frame-data did not return the exact PNG")

            stale_status = None
            try:
                get(f"{base}/frame-data?version=stale-value", timeout=30)
            except urllib.error.HTTPError as exc:
                stale_status = exc.code
            print("  stale version:", stale_status, "(409 expected)")
            if stale_status != 409:
                failures.append("stale version was not rejected with 409")

        map_code, mapped = get(f"{base}/map?x=3000&y=10&width=3968&height=1152")
        print(f"\nGET /map (dead band) -> {map_code}")
        print("  in_dead_band :", mapped.get("in_dead_band"))
        live_code, live = get(f"{base}/map?x=3000&y=600&width=3968&height=1152")
        print("  live point   :", live.get("logical"))
        if not mapped.get("in_dead_band"):
            failures.append("dead band not reported at (3000,10)")
        if not live.get("logical"):
            failures.append("live point did not map at (3000,600)")
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    print("\n" + "=" * 60)
    if failures:
        print("E2E FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("E2E PASSED — every assertion above came from a real HTTP response")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
