from __future__ import annotations

import asyncio
import ssl
from typing import Any


def insecure_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def open_rfb(descriptor: dict) -> Any:
    from hermes_computer_bridge.rfb_client import RfbClient

    kind = descriptor.get("kind")
    if kind == "vm":
        from hermes_computer_bridge.proxmox_client import ProxmoxClient
        from hermes_computer_bridge.targets import proxmox_config

        cfg = proxmox_config()
        if not cfg:
            raise RuntimeError("Proxmox not configured")
        vmid = descriptor["vmid"]
        client = ProxmoxClient(cfg["url"], cfg["token"])
        proxy = await asyncio.to_thread(client.vncproxy, cfg["node"], vmid)
        uri = client.websocket_uri(cfg["node"], vmid, proxy["port"], proxy["ticket"])
        rfb = RfbClient(
            uri,
            proxy["ticket"].encode(),
            headers={"Authorization": cfg["token"]},
            ssl_context=insecure_ssl(),
        )
    elif kind == "vnc":
        from hermes_computer_bridge.targets import resolve_vnc_uri

        uri, password = resolve_vnc_uri(descriptor["endpoint"])
        rfb = RfbClient(uri, password.encode())
    else:
        raise RuntimeError(f"not a remote target: {kind!r}")

    try:
        await asyncio.wait_for(rfb.connect(), timeout=12)
    except asyncio.TimeoutError:
        try:
            await rfb.close()
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("VNC connect timed out (no server, wrong port, or not a VNC server?)")
    except Exception:
        try:
            await rfb.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return rfb


__all__ = ["open_rfb", "insecure_ssl"]
