"""Verify the plugin is discovered + gated exactly as the web server sees it."""
import os
import sys

os.environ["HERMES_HOME"] = "/home/vigeron/.hermes"
sys.path.insert(0, "/home/vigeron/.hermes/hermes-agent")

from hermes_cli.plugins_cmd import _get_enabled_set, _get_disabled_set

enabled = _get_enabled_set()
disabled = _get_disabled_set()
NAME = "hermes-computer-bridge"
print("enabled_set contains:", NAME in enabled)
print("disabled_set contains:", NAME in disabled)

from hermes_cli.web_server import _get_dashboard_plugins

for p in _get_dashboard_plugins():
    if p.get("name") == NAME:
        print("DISCOVERED")
        print("  source   :", p.get("source"))
        print("  _api_file:", p.get("_api_file"))
        print("  _dir     :", p.get("_dir"))
        gate_ok = p.get("source") != "user" or (NAME in enabled and NAME not in disabled)
        print("  would_mount:", gate_ok)
        break
else:
    print("NOT DISCOVERED by _get_dashboard_plugins")
