#!/usr/bin/env python3
"""Real end-to-end run through the provider ABC (not the helper directly).

Proves the step-2 stack: CaptureService -> ladder -> PortalCapture ->
helper -> PNG, plus the geometry description that hangs off the frame.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_computer_bridge.capture_service import CaptureService  # noqa: E402

out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "evidence" / "abc-frame.png"

svc = CaptureService()

print("=== probe (no session, no dialog) ===")
probe = svc.probe()
print(json.dumps(probe, indent=2)[:1200])

print("\n=== capture through ABC ===")
frame, result = svc.capture(out, timeout_s=120)
print("used_rung:", result.used_rung)
for a in result.attempts:
    print(f"  attempt rung={a.rung} ok={a.ok} kind={a.kind} err={a.error}")

if frame is None:
    print("NO FRAME")
    raise SystemExit(1)

print("\n=== describe_frame ===")
print(json.dumps(svc.describe_frame(frame), indent=2))

assert frame.path.is_file(), "provider returned a Frame with no file"
print("\nOK file exists:", frame.path, frame.path.stat().st_size, "bytes")
