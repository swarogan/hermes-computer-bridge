from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def config_file() -> Path:
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(root) / "hermes-computer-bridge" / "proxmox.json"


def proxmox_config(
    env: Optional[dict] = None, path: Optional[Path] = None
) -> Optional[dict]:
    env = os.environ if env is None else env
    url = env.get("PROXMOX_URL")
    token = env.get("PROXMOX_TOKEN")
    node = env.get("PROXMOX_NODE")
    if url and token and node:
        return {"url": url, "token": token, "node": node}
    file = config_file() if path is None else Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("url") and data.get("token") and data.get("node"):
        return {"url": data["url"], "token": data["token"], "node": data["node"]}
    return None


def parse_target(target: Optional[str]) -> dict:
    if not target or target == "local":
        return {"kind": "local", "id": "local"}
    if target.startswith("vm:"):
        rest = target[3:]
        try:
            vmid = int(rest)
        except ValueError as exc:
            raise ValueError(f"bad vm target: {target!r}") from exc
        return {"kind": "vm", "id": target, "vmid": vmid}
    raise ValueError(f"unknown target: {target!r}")


def list_targets(
    env: Optional[dict] = None, client_factory=None, path: Optional[Path] = None
) -> list[dict]:
    targets = [{"id": "local", "label": "Local desktop", "kind": "local"}]
    cfg = proxmox_config(env, path)
    if not cfg:
        return targets
    try:
        if client_factory is None:
            from hermes_computer_bridge.proxmox_client import ProxmoxClient

            client = ProxmoxClient(cfg["url"], cfg["token"])
        else:
            client = client_factory(cfg)
        for vm in client.vms(cfg["node"]):
            if vm.get("status") == "running":
                targets.append(
                    {
                        "id": f"vm:{vm['vmid']}",
                        "label": f"VM {vm['vmid']} ({vm.get('name', '')})",
                        "kind": "vm",
                        "vmid": vm["vmid"],
                    }
                )
    except Exception:
        pass
    return targets


__all__ = ["proxmox_config", "parse_target", "list_targets"]
