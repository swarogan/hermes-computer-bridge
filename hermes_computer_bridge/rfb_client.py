from __future__ import annotations

import asyncio
import io
import struct
import zlib
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


QEMU_EXT_KEY_EVENT = -258
# Gap between consecutive key events. Enough for the guest's input stack to
# keep up (~8ms is well under human typing speed, so a 20-character command
# costs ~0.3s), small enough that nobody waits on it.
KEY_EVENT_GAP_S = 0.008
# How long the guest gets to consume a paste before we hand its clipboard back.
CLIPBOARD_SETTLE_S = 0.4


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
        self.clipboard = ""
        self._ws: Any = None
        self._buf = b""
        self._input = RfbInput()
        self._fb: Any = None
        self._zlib: Any = None

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
        # -258 asks for QEMU's Extended Key Event. The server answers by sending
        # a zero-sized pseudo-rectangle with that encoding, which is the only
        # signal we get; until it arrives, keys go out as plain keysyms.
        await self._ws.send(set_encodings_msg([6, 1, 0, QEMU_EXT_KEY_EVENT]))
        from PIL import Image

        self._fb = Image.new("RGB", (self.width, self.height))
        self._zlib = zlib.decompressobj()
        # The -258 confirmation only ever arrives inside a FramebufferUpdate, so
        # draw one out now and read it here. AgentVnc — which carries every
        # keystroke the panel sends to a VM — only ever writes to its socket, so
        # without this its keys would go out on the keysym path forever and AltGr
        # (ISO_Level3_Shift) would be dropped by QEMU on every press.
        await self.request_full()
        while not await self._read_message():
            continue
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
        """Deliver one command, pacing keystrokes so the guest keeps up.

        Typing used to go out as fast as the socket accepted it — a dozen key
        events inside a millisecond. The guest processes them through QEMU, a
        virtual keyboard and its compositor before the focused app ever sees
        them, and it drops what it cannot keep up with: measured as `ls`
        vanishing entirely and `-R -n` arriving as `--`.

        Pointer commands are deliberately NOT paced. A press/release pair must
        stay tight or it reads as a slow click, and motion must not lag.
        """
        messages = self._input.encode(cmd)
        paced = KEY_EVENT_GAP_S if str(cmd.get("op")) in ("text", "key") else 0.0
        for index, message in enumerate(messages):
            if paced and index:
                await asyncio.sleep(paced)
            await self._ws.send(message)

    async def set_clipboard(self, text: str) -> None:
        data = text.encode("latin-1", "replace")
        await self._ws.send(struct.pack(">BxxxI", 6, len(data)) + data)

    async def paste(self, text: str, *, settle_s: float = CLIPBOARD_SETTLE_S) -> None:
        """Deliver `text` in one packet instead of one keystroke per character.

        Typing sends two RFB messages per character and the guest drops what it
        cannot keep up with — `ls` vanished, `-R -n` arrived as `--`. The
        clipboard carries the whole string in a single ClientCutText, so there
        is no stream to lose, and the guest's keyboard layout stops mattering.

        The guest's own clipboard is restored afterwards: a human working in
        there should not find their copied text replaced by our command. The
        restore is best-effort — between the paste and it there is a window
        where the clipboard holds our text, and we only know the previous
        contents if the guest has sent them (ServerCutText).
        """
        previous = self.clipboard
        await self.set_clipboard(text)
        # Shift+Insert pastes in terminals and GUI apps alike, unlike Ctrl+V
        # which terminals reserve for something else.
        await self.send({"op": "key", "key": "Insert", "mods": ["shift"]})
        if previous:
            await asyncio.sleep(settle_s)
            await self.set_clipboard(previous)

    async def request_full(self) -> None:
        await self._ws.send(fb_update_request(0, 0, 0, self.width, self.height))

    async def request_incremental(self) -> None:
        await self._ws.send(fb_update_request(1, 0, 0, self.width, self.height))

    async def _apply_rects(self, count: int) -> None:
        from PIL import Image

        for _ in range(count):
            x, y, w, h, encoding = struct.unpack(">HHHHi", await self._recvn(12))
            if encoding == QEMU_EXT_KEY_EVENT:
                # Not pixels: the server confirming it accepts physical keycodes.
                self._input.qemu_ext_key = True
                continue
            if w == 0 or h == 0:
                continue
            if encoding == 0:
                data = await self._recvn(w * h * 4)
                self._fb.paste(Image.frombytes("RGB", (w, h), data, "raw", "BGRX"), (x, y))
            elif encoding == 1:
                sx, sy = struct.unpack(">HH", await self._recvn(4))
                region = self._fb.crop((sx, sy, sx + w, sy + h))
                self._fb.paste(region, (x, y))
            elif encoding == 6:
                length = struct.unpack(">I", await self._recvn(4))[0]
                raw = self._zlib.decompress(await self._recvn(length))
                self._fb.paste(Image.frombytes("RGB", (w, h), raw, "raw", "BGRX"), (x, y))
            else:
                raise RuntimeError(f"unsupported RFB encoding {encoding}")

    async def _read_message(self) -> bool:
        """Read one server message, applying a framebuffer update if that is
        what it is. Returns True when a framebuffer update was applied."""
        message_type = (await self._recvn(1))[0]
        if message_type == 0:
            header = await self._recvn(3)
            await self._apply_rects(struct.unpack(">H", header[1:3])[0])
            return True
        if message_type == 1:
            header = await self._recvn(5)
            await self._recvn(struct.unpack(">H", header[3:5])[0] * 6)
        elif message_type == 2:
            pass
        elif message_type == 3:
            header = await self._recvn(7)
            length = struct.unpack(">I", header[3:7])[0]
            self.clipboard = (await self._recvn(length)).decode("latin-1")
        else:
            raise RuntimeError(f"unexpected server message {message_type}")
        return False

    async def pump(self) -> bool:
        """Streaming: read one message and, on a framebuffer update, ask for
        the next incremental one."""
        updated = await self._read_message()
        if updated:
            await self.request_incremental()
        return updated

    def snapshot(self, *, fmt: str = "JPEG", quality: int = 90) -> bytes:
        buffer = io.BytesIO()
        if fmt == "PNG":
            self._fb.save(buffer, "PNG")
        else:
            self._fb.save(buffer, "JPEG", quality=quality)
        return buffer.getvalue()

    async def capture(self, *, quality: int = 90) -> bytes:
        await self.request_full()
        while not await self._read_message():
            continue
        return self.snapshot(quality=quality)

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
