from __future__ import annotations

import pytest

from hermes_computer_bridge.targets import list_targets, parse_target, proxmox_config


def test_parse_target_defaults_to_local():
    assert parse_target(None) == {"kind": "local", "id": "local"}
    assert parse_target("local") == {"kind": "local", "id": "local"}


def test_parse_target_reads_a_vm_id():
    assert parse_target("vm:112") == {"kind": "vm", "id": "vm:112", "vmid": 112}


def test_parse_target_rejects_garbage():
    with pytest.raises(ValueError):
        parse_target("vm:notanumber")
    with pytest.raises(ValueError):
        parse_target("nonsense")


def test_proxmox_config_needs_all_three_values(tmp_path):
    absent = tmp_path / "none.json"
    assert proxmox_config({}, path=absent) is None
    assert proxmox_config({"PROXMOX_URL": "x", "PROXMOX_TOKEN": "y"}, path=absent) is None
    cfg = proxmox_config({"PROXMOX_URL": "u", "PROXMOX_TOKEN": "t", "PROXMOX_NODE": "pve"}, path=absent)
    assert cfg == {"url": "u", "token": "t", "node": "pve"}


def test_proxmox_config_falls_back_to_a_file(tmp_path):
    import json

    file = tmp_path / "proxmox.json"
    file.write_text(json.dumps({"url": "u", "token": "t", "node": "pve"}))
    assert proxmox_config({}, path=file) == {"url": "u", "token": "t", "node": "pve"}


def test_list_targets_is_local_only_without_proxmox(tmp_path):
    absent = tmp_path / "none.json"
    assert list_targets(env={}, path=absent) == [
        {"id": "local", "label": "Local desktop", "kind": "local"}
    ]


def test_list_targets_adds_running_vms():
    class FakeClient:
        def vms(self, node):
            return [
                {"vmid": 112, "name": "mint-test", "status": "running"},
                {"vmid": 200, "name": "off", "status": "stopped"},
            ]

    env = {"PROXMOX_URL": "u", "PROXMOX_TOKEN": "t", "PROXMOX_NODE": "pve"}
    targets = list_targets(env=env, client_factory=lambda cfg: FakeClient())
    ids = [t["id"] for t in targets]
    assert ids == ["local", "vm:112"]
    assert targets[1]["label"] == "VM 112 (mint-test)"
