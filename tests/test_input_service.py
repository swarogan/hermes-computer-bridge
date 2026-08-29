from __future__ import annotations

import pytest

from hermes_computer_bridge.errors import CapabilityMissing
from hermes_computer_bridge.input_service import (
    InputService,
    ShellInput,
    wlrctl_commands,
    xdotool_commands,
)


def test_xdotool_move_is_absolute():
    assert xdotool_commands({"op": "move", "x": 10, "y": 20}) == [
        ["mousemove", "--", "10", "20"]
    ]


def test_xdotool_click_moves_then_clicks_the_named_button():
    assert xdotool_commands({"op": "click", "x": 5, "y": 6, "button": "right"}) == [
        ["mousemove", "--", "5", "6"],
        ["click", "3"],
    ]


def test_xdotool_button_press_and_release_are_separable():
    assert xdotool_commands({"op": "button", "button": "left", "state": "press"}) == [
        ["mousedown", "1"]
    ]
    assert xdotool_commands({"op": "button", "button": "left", "state": "release"}) == [
        ["mouseup", "1"]
    ]


def test_xdotool_text_and_key_and_chord():
    assert xdotool_commands({"op": "text", "text": "hi"}) == [["type", "--", "hi"]]
    assert xdotool_commands({"op": "key", "key": "Return"}) == [["key", "Return"]]
    assert xdotool_commands({"op": "key", "key": "c", "mods": ["ctrl"]}) == [
        ["key", "ctrl+c"]
    ]


def test_wlrctl_maps_pointer_and_keyboard():
    assert wlrctl_commands({"op": "move", "x": 1, "y": 2}) == [
        ["pointer", "move", "1", "2"]
    ]
    assert wlrctl_commands({"op": "click", "button": "middle"}) == [
        ["pointer", "click", "middle"]
    ]
    assert wlrctl_commands({"op": "text", "text": "hi"}) == [
        ["keyboard", "type", "hi"]
    ]
    assert wlrctl_commands({"op": "key", "key": "Return"}) == [
        ["keyboard", "key", "Return"]
    ]


def test_provider_probe_is_false_without_its_tool():
    provider = ShellInput(
        "XTest", "xdotool", xdotool_commands, which=lambda _tool: None, env={"DISPLAY": ":0"}
    )
    assert provider.probe() is False


def test_provider_probe_needs_display_for_xtest():
    provider = ShellInput(
        "XTest", "xdotool", xdotool_commands, which=lambda _tool: "/usr/bin/xdotool", env={}
    )
    assert provider.probe() is False


def test_provider_injects_by_running_the_tool():
    runs = []
    provider = ShellInput(
        "XTest",
        "xdotool",
        xdotool_commands,
        which=lambda _tool: "/usr/bin/xdotool",
        env={"DISPLAY": ":0"},
        run=lambda argv: runs.append(argv),
    )
    assert provider.probe() is True
    provider.inject({"op": "click", "x": 1, "y": 2})
    assert runs == [
        ["xdotool", "mousemove", "--", "1", "2"],
        ["xdotool", "click", "1"],
    ]


def _fake(name, ok, runs):
    return ShellInput(
        name,
        name,
        xdotool_commands,
        which=lambda _t: ("/bin/" + name) if ok else None,
        env={"DISPLAY": ":0"},
        run=lambda argv: runs.append(argv),
    )


def test_service_selects_the_first_available_rung():
    runs = []
    service = InputService([_fake("wlr", False, runs), _fake("xtest", True, runs)])
    assert service.selected_name() == "xtest"


def test_service_injects_through_the_selected_rung():
    runs = []
    service = InputService([_fake("wlr", False, runs), _fake("xtest", True, runs)])
    service.inject({"op": "move", "x": 3, "y": 4})
    assert runs == [["xtest", "mousemove", "--", "3", "4"]]


def test_service_reports_capability_missing_when_no_rung_is_available():
    runs = []
    service = InputService([_fake("wlr", False, runs), _fake("xtest", False, runs)])
    assert service.selected_name() is None
    with pytest.raises(CapabilityMissing):
        service.inject({"op": "move", "x": 1, "y": 1})


def test_xdotool_drag_holds_the_button_across_the_move():
    assert xdotool_commands(
        {"op": "drag", "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4, "button": "left"}
    ) == [
        ["mousemove", "--", "1", "2"],
        ["mousedown", "1"],
        ["mousemove", "--", "3", "4"],
        ["mouseup", "1"],
    ]
