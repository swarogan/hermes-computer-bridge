"""Read the live desktop backend's session token from its own environment.

The token is generated per process and injected via
HERMES_DASHBOARD_SESSION_TOKEN, so /proc/<pid>/environ is the authoritative
source for a LOCAL probe. It is never printed or written anywhere.
"""
import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

PREFIX = "/api/plugins/hermes-computer-bridge"
DESKTOP_BACKEND_PID = 1456841  # `hermes_cli.main serve` spawned by the desktop app


def token_from_pid(pid: int):
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
    except PermissionError:
        return None
    for entry in raw.split("\0"):
        if entry.startswith("HERMES_DASHBOARD_SESSION_TOKEN="):
            return entry.split("=", 1)[1]
    return None


def port_from_pid(pid: int):
    out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f"pid={pid}" in line:
            m = re.search(r"127\.0\.0\.1:(\d+)", line)
            if m:
                return int(m.group(1))
    return None


pid = DESKTOP_BACKEND_PID
port = port_from_pid(pid)
token = token_from_pid(pid)
print(f"desktop backend pid={pid} port={port} token={'FOUND' if token else 'MISSING'}")

if not (port and token):
    raise SystemExit("cannot probe without port+token")

req = urllib.request.Request(f"http://127.0.0.1:{port}{PREFIX}/status")
req.add_header("X-Hermes-Session-Token", token)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read().decode())
    print(f"\nGET {PREFIX}/status -> {r.status}  ROUTES ARE MOUNTED")
    for key in ("ok", "frame_name", "frame_version", "frame_blank", "streams_are_cached"):
        print(f"   {key:18s}: {body.get(key)}")
    print(f"   {'outputs':18s}: {len(body.get('outputs') or [])}")
except urllib.error.HTTPError as e:
    print(f"\nGET {PREFIX}/status -> HTTP {e.code}")
    if e.code == 404:
        print("   NOT MOUNTED: this backend started before `hermes plugins enable`.")
        print("   Restart Hermes Desktop to mount the plugin router.")
    else:
        print("  ", e.read().decode()[:200])
