from __future__ import annotations

from pathlib import Path

from hermes_computer_bridge import live_registry

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "__init__.py").read_text(encoding="utf-8")


def test_input_tools_are_wired_not_stubbed():
    assert "not built" not in SRC
    assert "step 4" not in SRC.lower()
    assert "_inject(" in SRC
    for tool in ("computer_bridge_click", "computer_bridge_type", "computer_bridge_key"):
        assert f'name="{tool}"' in SRC


def test_input_dispatch_prefers_the_live_stream_then_the_ladder():
    assert "live_registry.get_current()" in SRC
    assert "stream.send(cmd)" in SRC
    assert "_input_service.inject(cmd)" in SRC
    assert "_vnc().send(info[\"vmid\"], cmd)" in SRC


def test_a_vm_target_routes_to_its_own_vnc_session():
    assert 'name="computer_bridge_targets"' in SRC
    assert "list_targets()" in SRC
    assert "parse_target(target)" in SRC
    assert 'info["kind"] == "vm"' in SRC
    assert "_vnc().screenshot(info[\"vmid\"])" in SRC


def test_live_registry_round_trips_and_clears():
    assert live_registry.get_current() is None
    sentinel = object()
    live_registry.set_current(sentinel)
    assert live_registry.get_current() is sentinel
    live_registry.set_current(None)
    assert live_registry.get_current() is None
