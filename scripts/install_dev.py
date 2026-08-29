#!/usr/bin/env python3
"""Idempotent dev install: symlink this repo into the Hermes plugin root.

A symlink (not a copy) so the repo stays the single source of truth and a
`git pull` is the whole update story. Re-running is safe: an existing correct
symlink is left alone, a stale one is repointed, and a real directory is
refused rather than silently destroyed.

Enabling the backend is NOT done here — that is `hermes plugins enable`,
the official gate (plugins.enabled in config.yaml). This script only puts the
package where Hermes looks for it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "hermes-computer-bridge"


def hermes_home(profile: str | None) -> Path:
    """Resolve HERMES_HOME the way Hermes itself does — never hardcode."""
    env = os.environ.get("HERMES_HOME")
    if env and not profile:
        return Path(env).expanduser()
    base = Path("~/.hermes").expanduser()
    if profile and profile != "default":
        return base / "profiles" / profile
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=None, help="Hermes profile (default: root home)")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    root = hermes_home(args.profile) / "plugins"
    link = root / NAME

    desktop_root = hermes_home(args.profile) / "desktop-plugins" / NAME
    desktop_link = desktop_root / "plugin.js"

    if args.uninstall:
        if link.is_symlink():
            link.unlink()
            print(f"removed symlink {link}")
        elif link.exists():
            print(f"REFUSING to delete real directory {link}", file=sys.stderr)
            return 1
        else:
            print(f"nothing to remove at {link}")
        if desktop_link.is_symlink() or desktop_link.is_file():
            desktop_link.unlink()
            print(f"removed {desktop_link}")
            try:
                desktop_root.rmdir()
            except OSError:
                pass
        return 0

    root.mkdir(parents=True, exist_ok=True)
    desktop_root.mkdir(parents=True, exist_ok=True)

    if link.is_symlink():
        current = Path(os.readlink(link))
        if current != REPO:
            link.unlink()
            link.symlink_to(REPO, target_is_directory=True)
            print(f"repointed {link} -> {REPO}")
        else:
            print(f"already installed: {link} -> {REPO}")
    elif link.exists():
        print(
            f"REFUSING to overwrite real directory {link}\n"
            "Remove it by hand if you meant to replace it.",
            file=sys.stderr,
        )
        return 1
    else:
        link.symlink_to(REPO, target_is_directory=True)
        print(f"installed: {link} -> {REPO}")

    # Standalone desktop door is default-on. Unified half stays opt-in.
    target = REPO / "desktop" / "plugin.js"
    if desktop_link.is_symlink() or desktop_link.is_file():
        desktop_link.unlink()
    desktop_link.symlink_to(target)
    print(f"installed desktop door: {desktop_link} -> {target}")
    print("\nNext (the official gate — this script does NOT enable anything):")
    print(f"  hermes plugins enable {NAME}")
    print("  Restart Hermes Desktop. The standalone desktop-plugins door loads on by default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
