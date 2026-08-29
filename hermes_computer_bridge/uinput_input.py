from __future__ import annotations

import fcntl
import os
import struct
import time
from typing import Optional

UINPUT = "/dev/uinput"

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_SET_ABSBIT = 0x40045567
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_ABS = 0x03
SYN_REPORT = 0x00
REL_WHEEL = 0x08
ABS_X = 0x00
ABS_Y = 0x01
ABS_MAX = 65535

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BUTTON = {"left": BTN_LEFT, "right": BTN_RIGHT, "middle": BTN_MIDDLE}

KEY_LEFTSHIFT = 42
KEY_LEFTCTRL = 29
KEY_LEFTALT = 56
KEY_LEFTMETA = 125

MOD = {
    "ctrl": KEY_LEFTCTRL,
    "control": KEY_LEFTCTRL,
    "shift": KEY_LEFTSHIFT,
    "alt": KEY_LEFTALT,
    "super": KEY_LEFTMETA,
    "meta": KEY_LEFTMETA,
    "win": KEY_LEFTMETA,
}

_LETTERS = {
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
    "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
    "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
}
_DIGITS = {"1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11}
_SYMBOL = {
    "-": (12, False), "=": (13, False), "[": (26, False), "]": (27, False),
    "\\": (43, False), ";": (39, False), "'": (40, False), "`": (41, False),
    ",": (51, False), ".": (52, False), "/": (53, False), " ": (57, False),
    "\n": (28, False), "\t": (15, False),
    "!": (2, True), "@": (3, True), "#": (4, True), "$": (5, True), "%": (6, True),
    "^": (7, True), "&": (8, True), "*": (9, True), "(": (10, True), ")": (11, True),
    "_": (12, True), "+": (13, True), "{": (26, True), "}": (27, True), "|": (43, True),
    ":": (39, True), '"': (40, True), "~": (41, True), "<": (51, True), ">": (52, True),
    "?": (53, True),
}

KEY_NAME = {
    "Return": 28, "Enter": 28, "Tab": 15, "BackSpace": 14, "Backspace": 14,
    "Escape": 1, "Esc": 1, "Delete": 111, "Insert": 110, "space": 57,
    "Home": 102, "End": 107, "Page_Up": 104, "Page_Down": 109,
    "Left": 105, "Right": 106, "Up": 103, "Down": 108,
    **{f"F{n}": (58 + n if n <= 10 else 76 + n) for n in range(1, 13)},
    "Ctrl": KEY_LEFTCTRL, "Shift": KEY_LEFTSHIFT, "Alt": KEY_LEFTALT, "Super": KEY_LEFTMETA,
}


def char_to_keycode(char: str) -> tuple[int, bool]:
    if char in _LETTERS:
        return _LETTERS[char], False
    upper = char.lower()
    if char.isupper() and upper in _LETTERS:
        return _LETTERS[upper], True
    if char in _DIGITS:
        return _DIGITS[char], False
    if char in _SYMBOL:
        return _SYMBOL[char]
    raise ValueError(f"no keycode for {char!r}")


def key_to_keycode(name: str) -> int:
    if name in KEY_NAME:
        return KEY_NAME[name]
    if name in MOD:
        return MOD[name]
    if len(name) == 1:
        return char_to_keycode(name)[0]
    raise ValueError(f"unknown key: {name!r}")


class UInput:
    def __init__(self, name: str = "hermes-computer-bridge") -> None:
        self._fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_REL)
        fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_ABS)
        fcntl.ioctl(self._fd, UI_SET_EVBIT, EV_SYN)
        for code in range(1, 256):
            fcntl.ioctl(self._fd, UI_SET_KEYBIT, code)
        for code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
            fcntl.ioctl(self._fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self._fd, UI_SET_RELBIT, REL_WHEEL)
        fcntl.ioctl(self._fd, UI_SET_ABSBIT, ABS_X)
        fcntl.ioctl(self._fd, UI_SET_ABSBIT, ABS_Y)
        absmax = [0] * 64
        absmax[ABS_X] = ABS_MAX
        absmax[ABS_Y] = ABS_MAX
        dev = struct.pack("80sHHHHi", name.encode(), 3, 0x1234, 0x5678, 1, 0)
        dev += struct.pack("64i", *absmax)
        dev += struct.pack("64i", *([0] * 64))
        dev += struct.pack("64i", *([0] * 64))
        dev += struct.pack("64i", *([0] * 64))
        os.write(self._fd, dev)
        fcntl.ioctl(self._fd, UI_DEV_CREATE)
        time.sleep(0.4)

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self._fd, struct.pack("llHHi", 0, 0, etype, code, value))

    def _syn(self) -> None:
        self._emit(EV_SYN, SYN_REPORT, 0)

    def move_norm(self, nx: float, ny: float) -> None:
        self._emit(EV_ABS, ABS_X, max(0, min(ABS_MAX, round(nx * ABS_MAX))))
        self._emit(EV_ABS, ABS_Y, max(0, min(ABS_MAX, round(ny * ABS_MAX))))
        self._syn()

    def button(self, code: int, down: bool) -> None:
        self._emit(EV_KEY, code, 1 if down else 0)
        self._syn()

    def scroll(self, ticks: int) -> None:
        self._emit(EV_REL, REL_WHEEL, ticks)
        self._syn()

    def key(self, code: int, down: bool) -> None:
        self._emit(EV_KEY, code, 1 if down else 0)
        self._syn()

    def close(self) -> None:
        try:
            fcntl.ioctl(self._fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self._fd)


class UinputInput:
    name = "uinput"

    def __init__(self, frame_path: Optional[str] = None, *, device_factory=UInput) -> None:
        self.frame_path = frame_path
        self._factory = device_factory
        self._dev: Optional[UInput] = None
        self._dims: Optional[tuple[int, int]] = None

    def probe(self) -> bool:
        try:
            fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return False
        os.close(fd)
        return True

    def _device(self) -> UInput:
        if self._dev is None:
            self._dev = self._factory()
        return self._dev

    def _frame_dims(self) -> tuple[int, int]:
        if self._dims is not None:
            return self._dims
        if self.frame_path:
            try:
                from PIL import Image

                with Image.open(self.frame_path) as image:
                    self._dims = image.size
                    return self._dims
            except Exception:  # noqa: BLE001
                pass
        self._dims = (1, 1)
        return self._dims

    def _norm(self, x: float, y: float) -> tuple[float, float]:
        w, h = self._frame_dims()
        return (float(x) / max(1, w), float(y) / max(1, h))

    def inject(self, cmd: dict) -> None:
        dev = self._device()
        op = cmd.get("op")
        if op == "move":
            dev.move_norm(*self._norm(cmd["x"], cmd["y"]))
        elif op == "click":
            if cmd.get("x") is not None and cmd.get("y") is not None:
                dev.move_norm(*self._norm(cmd["x"], cmd["y"]))
            code = BUTTON.get(str(cmd.get("button", "left")), BTN_LEFT)
            dev.button(code, True)
            dev.button(code, False)
        elif op == "button":
            code = BUTTON.get(str(cmd.get("button", "left")), BTN_LEFT)
            dev.button(code, cmd.get("state") == "press")
        elif op == "drag":
            code = BUTTON.get(str(cmd.get("button", "left")), BTN_LEFT)
            dev.move_norm(*self._norm(cmd["from_x"], cmd["from_y"]))
            dev.button(code, True)
            dev.move_norm(*self._norm(cmd["to_x"], cmd["to_y"]))
            dev.button(code, False)
        elif op == "scroll":
            dy = float(cmd.get("dy", 0.0))
            if dy:
                dev.scroll(-1 if dy > 0 else 1)
        elif op == "text":
            for char in str(cmd.get("text", "")):
                code, shift = char_to_keycode(char)
                if shift:
                    dev.key(KEY_LEFTSHIFT, True)
                dev.key(code, True)
                dev.key(code, False)
                if shift:
                    dev.key(KEY_LEFTSHIFT, False)
        elif op == "key":
            mods = [key_to_keycode(str(m)) for m in cmd.get("mods", [])]
            code = key_to_keycode(str(cmd["key"]))
            for m in mods:
                dev.key(m, True)
            dev.key(code, True)
            dev.key(code, False)
            for m in reversed(mods):
                dev.key(m, False)
        else:
            raise ValueError(f"unknown op: {op!r}")


__all__ = ["UInput", "UinputInput", "char_to_keycode", "key_to_keycode", "BUTTON"]
