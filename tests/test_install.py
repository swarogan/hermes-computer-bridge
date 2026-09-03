"""Installing into the root home only is why bots reported "I don't have that tool".

Every Hermes profile is its own HERMES_HOME with its own plugins/ directory and
does NOT inherit ~/.hermes/plugins. Measured: all 11 profiles on this machine
lacked the plugin, so PluginManager never called register() for any bot — no
tools, no hooks, no system-prompt section — and the model fell back to
computer_use, which drives the local desktop.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def installer():
    spec = importlib.util.spec_from_file_location("install_dev", ROOT / "scripts" / "install_dev.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discover_profiles_finds_every_bot(installer, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".hermes" / "profiles" / "tom").mkdir(parents=True)
    (home / ".hermes" / "profiles" / "luiza").mkdir(parents=True)
    (home / ".hermes" / "profiles" / "notes.txt").parent.mkdir(exist_ok=True)
    (home / ".hermes" / "profiles" / "notes.txt").write_text("not a profile")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert installer.discover_profiles() == ["luiza", "tom"]


def test_discover_profiles_survives_a_machine_without_profiles(installer, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert installer.discover_profiles() == []


def test_profile_home_is_not_the_root_home(installer, monkeypatch, tmp_path):
    """The whole bug in one assertion: a profile has its own plugins root."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert installer.hermes_home("tom") != installer.hermes_home(None)
    assert installer.hermes_home("tom").name == "tom"


def test_link_to_creates_a_link_not_a_copy(installer, tmp_path):
    """A copy would break `git pull` as the update story."""
    target = tmp_path / "repo"
    target.mkdir()
    (target / "marker.txt").write_text("source of truth")
    link = tmp_path / "link"

    how = installer.link_to(link, target, is_directory=True)
    assert how in {"symlink", "junction"}
    assert (link / "marker.txt").read_text() == "source of truth"

    # editing the repo must be visible through the link
    (target / "marker.txt").write_text("changed")
    assert (link / "marker.txt").read_text() == "changed"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_link_to_falls_back_only_on_windows(installer, tmp_path, monkeypatch):
    """On POSIX a failure must surface, not be silently worked around."""
    target = tmp_path / "repo"
    target.mkdir()

    def boom(*a, **k):
        raise OSError("no permission")

    monkeypatch.setattr(Path, "symlink_to", boom)
    with pytest.raises(OSError):
        installer.link_to(tmp_path / "link", target, is_directory=True)
