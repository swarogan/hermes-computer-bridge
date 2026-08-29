from __future__ import annotations

from typing import Any

BUTTONS: dict[str, int] = {"left": 272, "right": 273, "middle": 274}

MODIFIERS: dict[str, int] = {
    "ctrl": 0xFFE3,
    "control": 0xFFE3,
    "shift": 0xFFE1,
    "alt": 0xFFE9,
    "super": 0xFFEB,
    "meta": 0xFFEB,
    "win": 0xFFEB,
}

SPECIAL_KEYS: dict[str, int] = {
    "Return": 0xFF0D,
    "Enter": 0xFF0D,
    "Tab": 0xFF09,
    "BackSpace": 0xFF08,
    "Backspace": 0xFF08,
    "Escape": 0xFF1B,
    "Esc": 0xFF1B,
    "Delete": 0xFFFF,
    "Insert": 0xFF63,
    "Home": 0xFF50,
    "End": 0xFF57,
    "Page_Up": 0xFF55,
    "Page_Down": 0xFF56,
    "Left": 0xFF51,
    "Up": 0xFF52,
    "Right": 0xFF53,
    "Down": 0xFF54,
    "space": 0x20,
    "Ctrl": 0xFFE3,
    "Shift": 0xFFE1,
    "Alt": 0xFFE9,
    "Super": 0xFFEB,
    **{f"F{n}": 0xFFBE + (n - 1) for n in range(1, 13)},
}


def char_to_keysym(char: str) -> int:
    if len(char) != 1:
        raise ValueError(f"not a single character: {char!r}")
    cp = ord(char)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 + cp


def key_to_keysym(name: str) -> int:
    if name in SPECIAL_KEYS:
        return SPECIAL_KEYS[name]
    if name in MODIFIERS:
        return MODIFIERS[name]
    if len(name) == 1:
        return char_to_keysym(name)
    raise ValueError(f"unknown key: {name!r}")


def _button_code(name: Any) -> int:
    code = BUTTONS.get(str(name or "left"))
    if code is None:
        raise ValueError(f"unknown button: {name!r}")
    return code


def input_calls(cmd: dict, node_id: int) -> list[tuple[str, tuple]]:
    op = cmd.get("op")
    if not op:
        raise ValueError("missing op")

    if op == "move":
        return [("NotifyPointerMotionAbsolute", (node_id, float(cmd["x"]), float(cmd["y"])))]

    if op == "click":
        code = _button_code(cmd.get("button"))
        calls: list[tuple[str, tuple]] = []
        if cmd.get("x") is not None and cmd.get("y") is not None:
            calls.append(
                ("NotifyPointerMotionAbsolute", (node_id, float(cmd["x"]), float(cmd["y"])))
            )
        calls.append(("NotifyPointerButton", (code, 1)))
        calls.append(("NotifyPointerButton", (code, 0)))
        return calls

    if op == "button":
        code = _button_code(cmd.get("button"))
        state = 1 if cmd.get("state") == "press" else 0
        return [("NotifyPointerButton", (code, state))]

    if op == "drag":
        code = _button_code(cmd.get("button"))
        return [
            ("NotifyPointerMotionAbsolute", (node_id, float(cmd["from_x"]), float(cmd["from_y"]))),
            ("NotifyPointerButton", (code, 1)),
            ("NotifyPointerMotionAbsolute", (node_id, float(cmd["to_x"]), float(cmd["to_y"]))),
            ("NotifyPointerButton", (code, 0)),
        ]

    if op == "scroll":
        return [("NotifyPointerAxis", (float(cmd.get("dx", 0.0)), float(cmd.get("dy", 0.0))))]

    if op == "text":
        out: list[tuple[str, tuple]] = []
        for char in str(cmd.get("text", "")):
            keysym = char_to_keysym(char)
            out.append(("NotifyKeyboardKeysym", (keysym, 1)))
            out.append(("NotifyKeyboardKeysym", (keysym, 0)))
        return out

    if op == "key":
        keysym = key_to_keysym(str(cmd["key"]))
        state = cmd.get("state")
        if state == "press":
            return [("NotifyKeyboardKeysym", (keysym, 1))]
        if state == "release":
            return [("NotifyKeyboardKeysym", (keysym, 0))]
        mods = [key_to_keysym(str(m)) for m in cmd.get("mods", [])]
        seq: list[tuple[str, tuple]] = [("NotifyKeyboardKeysym", (m, 1)) for m in mods]
        seq.append(("NotifyKeyboardKeysym", (keysym, 1)))
        seq.append(("NotifyKeyboardKeysym", (keysym, 0)))
        seq.extend(("NotifyKeyboardKeysym", (m, 0)) for m in reversed(mods))
        return seq

    raise ValueError(f"unknown op: {op!r}")


__all__ = [
    "BUTTONS",
    "MODIFIERS",
    "SPECIAL_KEYS",
    "char_to_keysym",
    "key_to_keysym",
    "input_calls",
]
