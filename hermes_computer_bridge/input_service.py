from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Callable, Optional, Sequence

from hermes_computer_bridge.errors import CapabilityMissing

XDO_BUTTON = {"left": "1", "middle": "2", "right": "3"}


def xdotool_commands(cmd: dict) -> list[list[str]]:
    op = cmd.get("op")
    if op == "move":
        return [["mousemove", "--", str(int(cmd["x"])), str(int(cmd["y"]))]]
    if op == "click":
        out: list[list[str]] = []
        if cmd.get("x") is not None and cmd.get("y") is not None:
            out.append(["mousemove", "--", str(int(cmd["x"])), str(int(cmd["y"]))])
        out.append(["click", XDO_BUTTON.get(str(cmd.get("button", "left")), "1")])
        return out
    if op == "button":
        action = "mousedown" if cmd.get("state") == "press" else "mouseup"
        return [[action, XDO_BUTTON.get(str(cmd.get("button", "left")), "1")]]
    if op == "drag":
        b = XDO_BUTTON.get(str(cmd.get("button", "left")), "1")
        return [
            ["mousemove", "--", str(int(cmd["from_x"])), str(int(cmd["from_y"]))],
            ["mousedown", b],
            ["mousemove", "--", str(int(cmd["to_x"])), str(int(cmd["to_y"]))],
            ["mouseup", b],
        ]
    if op == "scroll":
        dy = float(cmd.get("dy", 0.0))
        if not dy:
            return []
        return [["click", "4" if dy < 0 else "5"]]
    if op == "text":
        return [["type", "--", str(cmd.get("text", ""))]]
    if op == "key":
        keyname = str(cmd["key"])
        mods = [str(m) for m in cmd.get("mods", [])]
        combo = "+".join(mods + [keyname]) if mods else keyname
        state = cmd.get("state")
        if state == "press":
            return [["keydown", combo]]
        if state == "release":
            return [["keyup", combo]]
        return [["key", combo]]
    raise ValueError(f"unknown op: {op!r}")


def wlrctl_commands(cmd: dict) -> list[list[str]]:
    op = cmd.get("op")
    if op == "move":
        return [["pointer", "move", str(int(cmd["x"])), str(int(cmd["y"]))]]
    if op == "click":
        return [["pointer", "click", str(cmd.get("button", "left"))]]
    if op == "button":
        if cmd.get("state") == "press":
            return [["pointer", "click", str(cmd.get("button", "left"))]]
        return []
    if op == "drag":
        return [
            ["pointer", "move", str(int(cmd["from_x"])), str(int(cmd["from_y"]))],
            ["pointer", "move", str(int(cmd["to_x"])), str(int(cmd["to_y"]))],
        ]
    if op == "scroll":
        return [["pointer", "scroll", str(cmd.get("dy", 0.0)), str(cmd.get("dx", 0.0))]]
    if op == "text":
        return [["keyboard", "type", str(cmd.get("text", ""))]]
    if op == "key":
        keyname = str(cmd["key"])
        mods = [str(m) for m in cmd.get("mods", [])]
        combo = "+".join(mods + [keyname]) if mods else keyname
        return [["keyboard", "key", combo]]
    raise ValueError(f"unknown op: {op!r}")


def _default_run(argv: Sequence[str]) -> None:
    subprocess.run(list(argv), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ShellInput:
    def __init__(
        self,
        name: str,
        tool: str,
        builder: Callable[[dict], list[list[str]]],
        *,
        which: Callable[[str], Optional[str]] = shutil.which,
        env: Optional[dict] = None,
        run: Callable[[Sequence[str]], None] = _default_run,
        requires_env: Optional[str] = "DISPLAY",
    ) -> None:
        self.name = name
        self.tool = tool
        self._builder = builder
        self._which = which
        self._env = os.environ if env is None else env
        self._run = run
        self.requires_env = requires_env

    def probe(self) -> bool:
        if not self._which(self.tool):
            return False
        if self.requires_env and not self._env.get(self.requires_env):
            return False
        return True

    def inject(self, cmd: dict) -> None:
        for args in self._builder(cmd):
            self._run([self.tool, *args])


class InputService:
    def __init__(self, providers: Sequence[Any]) -> None:
        self.providers = list(providers)

    def selected(self) -> Optional[Any]:
        for provider in self.providers:
            if provider.probe():
                return provider
        return None

    def selected_name(self) -> Optional[str]:
        provider = self.selected()
        return provider.name if provider else None

    def status(self) -> list[dict]:
        return [{"rung": p.name, "available": p.probe()} for p in self.providers]

    def inject(self, cmd: dict) -> str:
        provider = self.selected()
        if provider is None:
            raise CapabilityMissing("no input rung is available")
        provider.inject(cmd)
        return provider.name


def default_input_service(env: Optional[dict] = None) -> InputService:
    resolved = os.environ if env is None else env
    from pathlib import Path

    from hermes_computer_bridge.uinput_input import UinputInput

    frame = str(Path(__file__).resolve().parent.parent / "evidence" / "live-frame.jpg")
    uinput = UinputInput(frame_path=frame)
    wlr = ShellInput(
        "wlr-virtual-pointer", "wlrctl", wlrctl_commands, env=resolved, requires_env="WAYLAND_DISPLAY"
    )
    xtest = ShellInput("XTest", "xdotool", xdotool_commands, env=resolved, requires_env="DISPLAY")
    return InputService([uinput, wlr, xtest])


__all__ = [
    "InputService",
    "ShellInput",
    "default_input_service",
    "wlrctl_commands",
    "xdotool_commands",
]
