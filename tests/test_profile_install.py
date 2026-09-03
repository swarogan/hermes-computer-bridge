"""Seeding new bots, and the guard rails on writing into ~/.hermes.

Why this exists: the Desktop's create-bot form has no clone_all field
(tui_gateway/methods_profiles.py sends clone_config), so every bot made the
normal way starts with no plugins/ entry. Its register() is then never called
and the model falls back to computer_use — which drives the human's LOCAL
desktop instead of the VM. Installing once cannot fix that; tomorrow's bot is
empty again.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_computer_bridge import profile_install as pi


@pytest.fixture()
def hermes(monkeypatch, tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    monkeypatch.setattr(pi, "profiles_root", lambda: root)
    monkeypatch.delenv(pi.OPT_OUT_ENV, raising=False)
    return root


def _profile(root: Path, name: str) -> Path:
    d = root / name
    (d / "plugins").mkdir(parents=True)
    return d


def test_seeds_a_bot_that_has_no_plugins_entry(hermes, tmp_path):
    _profile(hermes, "inbox-triage")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "plugin.yaml").write_text("name: hermes-computer-bridge")

    assert pi.auto_install(repo) == ["inbox-triage"]
    linked = hermes / "inbox-triage" / "plugins" / pi.NAME
    assert (linked / "plugin.yaml").read_text() == "name: hermes-computer-bridge"


def test_is_idempotent(hermes, tmp_path):
    _profile(hermes, "tom")
    repo = tmp_path / "repo"
    repo.mkdir()

    assert pi.auto_install(repo) == ["tom"]
    assert pi.auto_install(repo) == [], "a second run must be a no-op"


def test_never_touches_a_profile_that_already_has_something_there(hermes, tmp_path):
    """Someone else's install — or a real directory — is not ours to repoint."""
    d = _profile(hermes, "tom")
    theirs = d / "plugins" / pi.NAME
    theirs.mkdir()
    (theirs / "marker").write_text("hand-installed")

    repo = tmp_path / "repo"
    repo.mkdir()
    assert pi.auto_install(repo) == []
    assert (theirs / "marker").read_text() == "hand-installed"
    assert not theirs.is_symlink()


def test_opt_out_env_disables_it(hermes, tmp_path, monkeypatch):
    _profile(hermes, "tom")
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv(pi.OPT_OUT_ENV, "1")
    assert pi.auto_install(repo) == []
    assert not (hermes / "tom" / "plugins" / pi.NAME).exists()


def test_one_broken_profile_does_not_stop_the_others(hermes, tmp_path, monkeypatch):
    _profile(hermes, "aaa")
    _profile(hermes, "zzz")
    repo = tmp_path / "repo"
    repo.mkdir()

    real = pi.link_to

    def flaky(link, target, *, is_directory):
        if "aaa" in str(link):
            raise OSError("permission denied")
        return real(link, target, is_directory=is_directory)

    monkeypatch.setattr(pi, "link_to", flaky)
    assert pi.auto_install(repo) == ["zzz"]


def test_reports_what_it_did(hermes, tmp_path):
    _profile(hermes, "tom")
    repo = tmp_path / "repo"
    repo.mkdir()
    lines: list[str] = []
    pi.auto_install(repo, log=lines.append)
    assert any("tom" in line for line in lines), "silent writes into ~/.hermes are not ok"


def test_survives_a_machine_without_profiles(hermes, tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "profiles_root", lambda: tmp_path / "nope")
    assert pi.discover_profiles() == []
    assert pi.auto_install(tmp_path) == []


def test_link_is_live_not_a_copy(hermes, tmp_path):
    _profile(hermes, "tom")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.txt").write_text("v1")
    pi.auto_install(repo)

    (repo / "f.txt").write_text("v2")
    seen = hermes / "tom" / "plugins" / pi.NAME / "f.txt"
    assert seen.read_text() == "v2", "git pull must remain the whole update story"
