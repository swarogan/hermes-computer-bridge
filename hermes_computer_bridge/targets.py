from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def _state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(root) / "hermes-computer-bridge"


def config_file() -> Path:
    return _state_dir() / "proxmox.json"


def vnc_file() -> Path:
    return _state_dir() / "vnc.json"


def vnc_endpoints(path: Optional[Path] = None) -> list[dict]:
    file = vnc_file() if path is None else Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if e.get("id") and e.get("host")] if isinstance(data, list) else []


def find_vnc_endpoint(endpoint_id: str, path: Optional[Path] = None) -> Optional[dict]:
    for endpoint in vnc_endpoints(path):
        if endpoint["id"] == endpoint_id:
            return endpoint
    return None


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
    if target.startswith("vnc:"):
        rest = target[4:]
        if not rest:
            raise ValueError(f"bad vnc target: {target!r}")
        return {"kind": "vnc", "id": target, "endpoint": rest}
    raise ValueError(f"unknown target: {target!r}")


def resolve_vnc_uri(endpoint: str, path: Optional[Path] = None) -> tuple[str, str]:
    """Return (uri, password) for a vnc endpoint id or an inline host[:port]."""
    saved = find_vnc_endpoint(endpoint, path)
    if saved:
        port = saved.get("port") or 5900
        return f"tcp://{saved['host']}:{port}", saved.get("password") or ""
    host, _, port = endpoint.partition(":")
    return f"tcp://{host}:{port or 5900}", ""


def list_targets(
    env: Optional[dict] = None, client_factory=None, path: Optional[Path] = None
) -> list[dict]:
    targets = [{"id": "local", "label": "Local desktop", "kind": "local"}]
    for endpoint in vnc_endpoints():
        targets.append(
            {
                "id": f"vnc:{endpoint['id']}",
                "label": endpoint.get("label") or endpoint["id"],
                "kind": "vnc",
            }
        )
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


def save_vnc_endpoint(endpoint: dict, path: Optional[Path] = None) -> None:
    file = vnc_file() if path is None else Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    existing = [e for e in vnc_endpoints(path) if e["id"] != endpoint["id"]]
    existing.append(endpoint)
    file.write_text(json.dumps(existing), encoding="utf-8")
    os.chmod(file, 0o600)


def delete_vnc_endpoint(endpoint_id: str, path: Optional[Path] = None) -> None:
    file = vnc_file() if path is None else Path(path)
    remaining = [e for e in vnc_endpoints(path) if e["id"] != endpoint_id]
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(remaining), encoding="utf-8")
    os.chmod(file, 0o600)


__all__ = [
    "proxmox_config",
    "parse_target",
    "list_targets",
    "vnc_endpoints",
    "find_vnc_endpoint",
    "resolve_vnc_uri",
    "save_vnc_endpoint",
    "delete_vnc_endpoint",
]
