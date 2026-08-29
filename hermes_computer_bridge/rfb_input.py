from __future__ import annotations

import struct

from hermes_computer_bridge.input_protocol import char_to_keysym, key_to_keysym

RFB_BUTTON = {"left": 1, "middle": 2, "right": 4}
WHEEL_UP = 8
WHEEL_DOWN = 16
SHIFT_KEYSYM = 0xFFE1
SHIFTED_SYMBOLS = set('~!@#$%^&*()_+{}|:"<>?')


def pointer_event(mask: int, x: int, y: int) -> bytes:
    return struct.pack(">BBHH", 5, mask & 0xFF, x & 0xFFFF, y & 0xFFFF)


def key_event(down: bool, keysym: int) -> bytes:
    return struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym & 0xFFFFFFFF)


class RfbInput:
    def __init__(self) -> None:
        self.x = 0
        self.y = 0
        self.mask = 0

    def encode(self, cmd: dict) -> list[bytes]:
        op = cmd.get("op")

        if op == "move":
            self.x = int(cmd["x"])
            self.y = int(cmd["y"])
            return [pointer_event(self.mask, self.x, self.y)]

        if op == "button":
            bit = RFB_BUTTON.get(str(cmd.get("button", "left")), 1)
            if cmd.get("state") == "press":
                self.mask |= bit
            else:
                self.mask &= ~bit
            return [pointer_event(self.mask, self.x, self.y)]

        if op == "click":
            if cmd.get("x") is not None and cmd.get("y") is not None:
                self.x = int(cmd["x"])
                self.y = int(cmd["y"])
            bit = RFB_BUTTON.get(str(cmd.get("button", "left")), 1)
            return [
                pointer_event(self.mask | bit, self.x, self.y),
                pointer_event(self.mask, self.x, self.y),
            ]

        if op == "scroll":
            dy = float(cmd.get("dy", 0.0))
            if not dy:
                return []
            wheel = WHEEL_UP if dy < 0 else WHEEL_DOWN
            return [
                pointer_event(self.mask | wheel, self.x, self.y),
                pointer_event(self.mask, self.x, self.y),
            ]

        if op == "drag":
            bit = RFB_BUTTON.get(str(cmd.get("button", "left")), 1)
            fx, fy = int(cmd["from_x"]), int(cmd["from_y"])
            tx, ty = int(cmd["to_x"]), int(cmd["to_y"])
            self.x, self.y = tx, ty
            return [
                pointer_event(self.mask, fx, fy),
                pointer_event(self.mask | bit, fx, fy),
                pointer_event(self.mask | bit, tx, ty),
                pointer_event(self.mask, tx, ty),
            ]

        if op == "text":
            out: list[bytes] = []
            for ch in str(cmd.get("text", "")):
                ks = char_to_keysym(ch)
                shift = ch in SHIFTED_SYMBOLS
                if shift:
                    out.append(key_event(True, SHIFT_KEYSYM))
                out.append(key_event(True, ks))
                out.append(key_event(False, ks))
                if shift:
                    out.append(key_event(False, SHIFT_KEYSYM))
            return out

        if op == "key":
            ks = key_to_keysym(str(cmd["key"]))
            state = cmd.get("state")
            if state == "press":
                return [key_event(True, ks)]
            if state == "release":
                return [key_event(False, ks)]
            mods = [key_to_keysym(str(m)) for m in cmd.get("mods", [])]
            out = [key_event(True, m) for m in mods]
            out.append(key_event(True, ks))
            out.append(key_event(False, ks))
            out.extend(key_event(False, m) for m in reversed(mods))
            return out

        raise ValueError(f"unknown op: {op!r}")


__all__ = ["RFB_BUTTON", "RfbInput", "pointer_event", "key_event"]
