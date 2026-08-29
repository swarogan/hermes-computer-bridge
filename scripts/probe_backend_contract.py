"""Probe the REAL backend contract: which interpreter mounts plugin_api.py."""
import importlib.util
import sys
from pathlib import Path

print("interpreter:", sys.executable)
print("python:", sys.version.split()[0])

for mod in ("fastapi", "pydantic", "uvicorn", "pytest"):
    spec = importlib.util.find_spec(mod)
    print(f"  {mod:10s}", "OK" if spec else "MISSING")

API = Path("/run/media/Workspace/Projekty/hermes-computer-bridge/dashboard/plugin_api.py")
name = "hermes_dashboard_plugin_hermes-computer-bridge"
spec = importlib.util.spec_from_file_location(name, API)
mod = importlib.util.module_from_spec(spec)
sys.modules[name] = mod  # mirrors web_server.py ordering
try:
    spec.loader.exec_module(mod)
except Exception as exc:
    sys.modules.pop(name, None)
    print("EXEC FAILED:", type(exc).__name__, exc)
    raise SystemExit(1)

router = getattr(mod, "router", None)
print("router:", type(router).__name__ if router else "MISSING")
for route in router.routes:
    methods = ",".join(sorted(getattr(route, "methods", {"WS"})))
    print(f"  {methods:10s} /api/plugins/hermes-computer-bridge{route.path}")
print("EXEC OK")
