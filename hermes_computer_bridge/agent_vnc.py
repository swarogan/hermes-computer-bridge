from __future__ import annotations

import asyncio
import threading
from typing import Any


class AgentVnc:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._clients: dict[str, Any] = {}

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _call(self, coro, timeout: float = 30.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    async def _ensure(self, descriptor: dict):
        key = descriptor["id"]
        client = self._clients.get(key)
        if client is not None:
            return client
        from hermes_computer_bridge.vnc_connect import open_rfb

        client = await open_rfb(descriptor)
        self._clients[key] = client
        return client

    def _drop(self, descriptor: dict) -> None:
        client = self._clients.pop(descriptor["id"], None)
        if client is not None:
            try:
                self._call(client.close(), timeout=5)
            except Exception:  # noqa: BLE001
                pass

    def send(self, descriptor: dict, cmd: dict) -> bool:
        try:
            self._call(self._send(descriptor, cmd))
            return True
        except Exception:  # noqa: BLE001
            self._drop(descriptor)
            return False

    async def _send(self, descriptor: dict, cmd: dict) -> None:
        rfb = await self._ensure(descriptor)
        await rfb.send(cmd)

    def screenshot(self, descriptor: dict) -> bytes:
        try:
            return self._call(self._screenshot(descriptor))
        except Exception:
            self._drop(descriptor)
            raise

    async def _screenshot(self, descriptor: dict) -> bytes:
        rfb = await self._ensure(descriptor)
        return await rfb.capture()

    def dimensions(self, descriptor: dict) -> tuple[int, int]:
        rfb = self._call(self._ensure(descriptor))
        return (rfb.width, rfb.height)


__all__ = ["AgentVnc"]
