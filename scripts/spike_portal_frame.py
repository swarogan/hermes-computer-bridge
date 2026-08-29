#!/usr/bin/python3
"""Step 1 spike wrapper. Delegates to helpers/portal_screencast.py."""
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "helpers" / "portal_screencast.py"
sys.argv = [str(HELPER), "spike", *sys.argv[1:]]
code = HELPER.read_text(encoding="utf-8")
ns = {"__name__": "__main__", "__file__": str(HELPER)}
exec(compile(code, str(HELPER), "exec"), ns)
