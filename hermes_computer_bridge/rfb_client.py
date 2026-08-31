from __future__ import annotations

import io
import struct
from typing import Any, Optional

from hermes_computer_bridge.rfb_input import RfbInput


def _rev_bits(byte: int) -> int:
    return int(f"{byte:08b}"[::-1], 2)


def vnc_key(password: bytes) -> bytes:
    key = (password + b"\x00" * 8)[:8]
    return bytes(_rev_bits(b) for b in key)


def vnc_auth_response(challenge: bytes, password: bytes) -> bytes:
    try:
        from cryptography.hazmat.decrepit.ciphers import algorithms
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
    except Exception:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = vnc_key(password)
    encryptor = Cipher(algorithms.TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


def set_pixel_format_msg() -> bytes:
    pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    return struct.pack(">B3x", 0) + pixel_format


def set_encodings_msg(encodings: list[int]) -> bytes:
    head = struct.pack(">BxH", 2, len(encodings))
    return head + b"".join(struct.pack(">i", e) for e in encodings)


def fb_update_request(incremental: int, x: int, y: int, w: int, h: int) -> bytes:
    return struct.pack(">BBHHHH", 3, incremental, x, y, w, h)


class _WsTransport:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def recv(self) -> bytes:
        data = await self._ws.recv()
        return data.encode() if isinstance(data, str) else data

    async def send(self, data: bytes) -> None:
        await self._ws.send(data)

    async def close(self) -> None:
        await self._ws.close()


class _TcpTransport:
    def __init__(self, reader: Any, writer: Any) -> None:
        self._reader = reader
        self._writer = writer

    async def recv(self) -> bytes:
        data = await self._reader.read(65536)
        if not data:
            raise ConnectionError("VNC connection closed")
        return data

    async def send(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        self._writer.close()


def _parse_tcp(uri: str) -> tuple[str, int]:
    rest = uri.split("://", 1)[1] if "://" in uri else uri
    host, _, port = rest.partition(":")
    return host, int(port or 5900)


class RfbClient:
    def __init__(
        self,
        uri: str,
        password: bytes,
        *,
        headers: Optional[dict] = None,
        ssl_context: Optional[Any] = None,
    ) -> None:
        self.uri = uri
        self.password = password
        self.headers = headers or {}
        self.ssl_context = ssl_context
        self.width = 0
        self.height = 0
        self.name = ""
        self._ws: Any = None
        self._buf = b""
        self._input = RfbInput()

    async def _recvn(self, n: int) -> bytes:
        while len(self._buf) < n:
            data = await self._ws.recv()
            if isinstance(data, str):
                data = data.encode()
            self._buf += data
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    async def connect(self) -> dict:
        if self.uri.startswith(("ws://", "wss://")):
            import websockets

            ws = await websockets.connect(
                self.uri,
                additional_headers=self.headers,
                ssl=self.ssl_context,
                max_size=None,
            )
            self._ws = _WsTransport(ws)
        else:
            import asyncio

            host, port = _parse_tcp(self.uri)
            reader, writer = await asyncio.open_connection(host, port, ssl=self.ssl_context)
            self._ws = _TcpTransport(reader, writer)

        await self._recvn(12)
        await self._ws.send(b"RFB 003.008\n")
        count = (await self._recvn(1))[0]
        if count == 0:
            reason_len = struct.unpack(">I", await self._recvn(4))[0]
            raise RuntimeError((await self._recvn(reason_len)).decode("utf-8", "replace"))
        types = await self._recvn(count)
        await self._authenticate(list(types))
        await self._ws.send(bytes([1]))
        head = await self._recvn(24)
        self.width, self.height = struct.unpack(">HH", head[:4])
        name_len = struct.unpack(">I", head[20:24])[0]
        self.name = (await self._recvn(name_len)).decode("utf-8", "replace")
        await self._ws.send(set_pixel_format_msg())
        await self._ws.send(set_encodings_msg([0]))
        return {"width": self.width, "height": self.height, "name": self.name}

    async def _authenticate(self, types: list[int]) -> None:
        if 2 in types and self.password:
            await self._ws.send(bytes([2]))
            challenge = await self._recvn(16)
            await self._ws.send(vnc_auth_response(challenge, self.password))
        elif 1 in types:
            await self._ws.send(bytes([1]))
        elif 2 in types:
            await self._ws.send(bytes([2]))
            challenge = await self._recvn(16)
            await self._ws.send(vnc_auth_response(challenge, self.password))
        else:
            raise RuntimeError(f"no supported VNC auth offered: {types}")
        result = struct.unpack(">I", await self._recvn(4))[0]
        if result != 0:
            reason = b""
            try:
                reason_len = struct.unpack(">I", await self._recvn(4))[0]
                reason = await self._recvn(reason_len)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(reason.decode("utf-8", "replace") or "VNC authentication failed")

    async def send(self, cmd: dict) -> None:
        for message in self._input.encode(cmd):
            await self._ws.send(message)

    async def _skip_non_framebuffer(self) -> None:
        while True:
            message_type = (await self._recvn(1))[0]
            if message_type == 0:
                return
            if message_type == 1:
                header = await self._recvn(5)
                colours = struct.unpack(">H", header[3:5])[0]
                await self._recvn(colours * 6)
            elif message_type == 2:
                continue
            elif message_type == 3:
                header = await self._recvn(7)
                length = struct.unpack(">I", header[3:7])[0]
                await self._recvn(length)
            else:
                raise RuntimeError(f"unexpected server message {message_type}")

    async def capture(self, *, quality: int = 90) -> bytes:
        from PIL import Image

        await self._ws.send(fb_update_request(0, 0, 0, self.width, self.height))
        await self._skip_non_framebuffer()
        header = await self._recvn(3)
        rectangles = struct.unpack(">H", header[1:3])[0]
        image = Image.new("RGB", (self.width, self.height))
        for _ in range(rectangles):
            x, y, w, h, encoding = struct.unpack(">HHHHi", await self._recvn(12))
            if encoding != 0:
                raise RuntimeError(f"unsupported RFB encoding {encoding}")
            data = await self._recvn(w * h * 4)
            rect = Image.frombytes("RGB", (w, h), data, "raw", "BGRX")
            image.paste(rect, (x, y))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality)
        return buffer.getvalue()

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


__all__ = [
    "RfbClient",
    "vnc_key",
    "vnc_auth_response",
    "set_pixel_format_msg",
    "set_encodings_msg",
    "fb_update_request",
]
