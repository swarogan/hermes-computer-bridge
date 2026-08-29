from __future__ import annotations

import asyncio
import os
import ssl
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


def _insecure_ssl() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def write_frame_atomically(path: Path, data: bytes) -> None:
    tmp = str(path) + ".part"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


class VncSession:
    def __init__(
        self,
        *,
        url: str,
        token: str,
        node: str,
        vmid: int,
        output: Path,
        on_frame: Optional[Callable[[Path], Awaitable[None]]] = None,
        fps: int = 8,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        self.url = url
        self.token = token
        self.node = node
        self.vmid = vmid
        self.output = Path(output)
        self.on_frame = on_frame
        self.fps = max(1, int(fps))
        self.ssl_context = ssl_context or _insecure_ssl()
        self.info: dict[str, Any] = {}
        self.last_error: Optional[str] = None
        self.frames = 0
        self._rfb: Any = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> dict[str, Any]:
        from hermes_computer_bridge.proxmox_client import ProxmoxClient
        from hermes_computer_bridge.rfb_client import RfbClient

        client = ProxmoxClient(self.url, self.token)
        proxy = await asyncio.to_thread(client.vncproxy, self.node, self.vmid)
        uri = client.websocket_uri(self.node, self.vmid, proxy["port"], proxy["ticket"])
        self._rfb = RfbClient(
            uri,
            proxy["ticket"].encode(),
            headers={"Authorization": self.token},
            ssl_context=self.ssl_context,
        )
        self.info = await self._rfb.connect()
        self._running = True
        self._task = asyncio.create_task(self._loop())
        return self.info

    async def _loop(self) -> None:
        interval = 1.0 / self.fps
        try:
            while self._running:
                jpg = await self._rfb.capture()
                write_frame_atomically(self.output, jpg)
                self.frames += 1
                if self.on_frame is not None:
                    await self.on_frame(self.output)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self._running = False

    async def send(self, cmd: dict) -> bool:
        if self._rfb is None or not self._running:
            return False
        try:
            await self._rfb.send(cmd)
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return False

    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._rfb is not None:
            try:
                await self._rfb.close()
            except Exception:  # noqa: BLE001
                pass
            self._rfb = None


__all__ = ["VncSession", "write_frame_atomically"]
