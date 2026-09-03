"""Put this plugin where every bot can see it.

Each Hermes profile is its own HERMES_HOME with its own ``plugins/`` directory
and does NOT inherit ``~/.hermes/plugins/``. Worse, the Desktop's "create bot"
form has no field for ``clone_all`` — ``tui_gateway/methods_profiles.py`` sends
``clone_config`` instead, which copies config, .env, SOUL.md and skills but not
plugins. So a bot made the normal way starts without this plugin, its
``register()`` is never called, and the model falls back to ``computer_use``,
which drives the human's LOCAL desktop instead of the VM.

Installing once cannot fix that: tomorrow's bot is empty again. Hence the
plugin seeds itself from the default profile, which is the only place it is
guaranteed to be running.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

NAME = "hermes-computer-bridge"
OPT_OUT_ENV = "HERMES_BRIDGE_NO_AUTO_INSTALL"


def link_to(link: Path, target: Path, *, is_directory: bool) -> str:
    """Point `link` at `target`, coping with Windows.

    A plain symlink needs Administrator or Developer Mode on Windows, which no
    installer can assume. A directory junction needs neither; a hard link does
    the same for a single file. Both keep the repo as the one source of truth,
    so `git pull` stays the whole update story — a copy would break that.
    """
    try:
        link.symlink_to(target, target_is_directory=is_directory)
        return "symlink"
    except OSError:
        if os.name != "nt":
            raise
        if is_directory:
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
            )
            return "junction"
        os.link(target, link)
        return "hardlink"


def profiles_root() -> Path:
    return Path("~/.hermes/profiles").expanduser()


def discover_profiles() -> list[str]:
    """Every profile on this machine, sorted."""
    base = profiles_root()
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def missing_profiles(repo: Path) -> list[str]:
    """Profiles whose plugins/ lacks this plugin.

    A profile that already has something at that name — a real directory a
    human put there, or a link elsewhere — is NOT reported. Repointing someone
    else's install is not this function's business.
    """
    missing = []
    for name in discover_profiles():
        entry = profiles_root() / name / "plugins" / NAME
        if not entry.exists() and not entry.is_symlink():
            missing.append(name)
    return missing


def auto_install(repo: Path, *, log=None) -> list[str]:
    """Link this plugin into any profile that lacks it. Returns those fixed.

    Deliberately narrow: it only ever CREATES a missing link. It never
    repoints, never overwrites, never deletes, and never touches config.yaml —
    enabling stays the human's decision through `hermes plugins enable`.
    Set HERMES_BRIDGE_NO_AUTO_INSTALL=1 to switch it off entirely.
    """
    if os.environ.get(OPT_OUT_ENV):
        return []
    fixed = []
    for name in missing_profiles(repo):
        target = profiles_root() / name / "plugins" / NAME
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            how = link_to(target, repo, is_directory=True)
        except (OSError, subprocess.SubprocessError) as exc:
            if log:
                log(f"auto-install failed for profile {name!r}: {exc!r}")
            continue
        fixed.append(name)
        if log:
            log(f"auto-installed into profile {name!r} ({how})")
    return fixed


__all__ = [
    "NAME",
    "OPT_OUT_ENV",
    "auto_install",
    "discover_profiles",
    "link_to",
    "missing_profiles",
    "profiles_root",
]
