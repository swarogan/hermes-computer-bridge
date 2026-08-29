from __future__ import annotations

import asyncio
import ssl
import threading
from typing import Any, Callable, Optional


class AgentVnc:
    def __init__(self, config_provider: Callable[[], Optional[dict]]) -> None:
        self._config = config_provider
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._clients: dict[int, Any] = {}

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout: float = 30.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    async def _ensure(self, vmid: int):
        client = self._clients.get(vmid)
        if client is not None:
            return client
        cfg = self._config()
        if not cfg:
            raise RuntimeError("Proxmox not configured")
        from hermes_computer_bridge.proxmox_client import ProxmoxClient
        from hermes_computer_bridge.rfb_client import RfbClient

        proxmox = ProxmoxClient(cfg["url"], cfg["token"])
        proxy = await asyncio.to_thread(proxmox.vncproxy, cfg["node"], vmid)
        uri = proxmox.websocket_uri(cfg["node"], vmid, proxy["port"], proxy["ticket"])
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        rfb = RfbClient(
            uri,
            proxy["ticket"].encode(),
            headers={"Authorization": cfg["token"]},
            ssl_context=context,
        )
        await rfb.connect()
        self._clients[vmid] = rfb
        return rfb

    def _drop(self, vmid: int) -> None:
        client = self._clients.pop(vmid, None)
        if client is not None:
            try:
                self._call(client.close(), timeout=5)
            except Exception:
                pass

    def send(self, vmid: int, cmd: dict) -> bool:
        try:
            self._call(self._send(vmid, cmd))
            return True
        except Exception:
            self._drop(vmid)
            return False

    async def _send(self, vmid: int, cmd: dict) -> None:
        rfb = await self._ensure(vmid)
        await rfb.send(cmd)

    def screenshot(self, vmid: int) -> bytes:
        try:
            return self._call(self._screenshot(vmid))
        except Exception:
            self._drop(vmid)
            raise

    async def _screenshot(self, vmid: int) -> bytes:
        rfb = await self._ensure(vmid)
        return await rfb.capture()

    def dimensions(self, vmid: int) -> tuple[int, int]:
        rfb = self._call(self._ensure(vmid))
        return (rfb.width, rfb.height)


__all__ = ["AgentVnc"]
