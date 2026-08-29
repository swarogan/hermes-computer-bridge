# hermes-computer-bridge

A Hermes Desktop plugin that gives Hermes agents a live view of your screen,
captured through `xdg-desktop-portal`. It works the same on Wayland (KDE,
GNOME, COSMIC, Sway, Hyprland) and on X11, because it asks the portal what it
can do instead of branching on which desktop environment is running.

![The Computer pane docked above Cronjobs in the Hermes Desktop right column, streaming DP-1 live](docs/computer-pane.png)

The panel mounts as a `Computer` tab in the right sidebar, docked directly
above Cronjobs, and starts the live stream on its own.

## Why a portal

The reference plugin it replaces branches `if X11 / elif wlroots / else Xorg`
and falls over on a plain Wayland session. This one speaks one protocol,
`xdg-desktop-portal`, so a single code path covers every modern compositor.
X11 remains as a lower rung, not the foundation.

## Capability ladder

Capture and input each fall through an ordered ladder. A rung is validated by
use, not by a binary existing on `PATH`. A read fails through to the next rung
only when the capability is genuinely missing; a transient error is a bounded
retry, never a hot loop.

```
CAPTURE                            INPUT
1. portal ScreenCast (PipeWire)    1. portal RemoteDesktop (libei)
2. wlr-screencopy                  2. wlroots virtual-pointer
3. X11 SHM                         3. XTest
4. remote RFB/RDP                  4. RFB/RDP input
```

`screenshot`, `click`, `type`, and `key` are one small API over whichever
rung answered. Declining the KDE consent prompt is not a missing capability,
so it does not fall through to X11.

## Consent and tokens

The KDE ScreenCast consent prompt cannot be bypassed; that is the Wayland
security model. `persist_mode=2` plus a `restore_token` stored under
`$XDG_STATE_HOME/hermes-computer-bridge/` reduces it to once per session or
until you revoke it. The token never enters git.

## Requirements

The capture helper needs the system Python with the GObject introspection and
GStreamer bindings (`gi`, `Gst`), which cannot be installed sensibly into a
venv. The plugin itself runs inside the Hermes venv and calls the helper as a
subprocess, so the venv and the agent never import `gi`.

| Runtime                    | gi  | Gst / pipewiresrc | Unix FD from the portal |
| -------------------------- | --- | ----------------- | ----------------------- |
| `/usr/bin/python3` (3.14)  | yes | yes               | yes                     |
| Hermes venv (3.11)         | no  | no                | no                      |

## Install

The repo is the source of truth; installation is a symlink, so `git pull` is
the whole update.

```bash
python3 scripts/install_dev.py --profile default   # idempotent symlink into ~/.hermes/plugins/
hermes plugins enable hermes-computer-bridge         # the official enable gate
# then restart Hermes Desktop; plugin routers mount once, at startup
```

The script never touches `config.yaml` and refuses to overwrite a real
directory. `scripts/install_dev.py --uninstall` removes only the symlink.
Validate the install with the official tool:

```bash
hermes plugins doctor . --ci
```

## HTTP surface

| Method | Path            | Purpose                                                    |
| ------ | --------------- | ---------------------------------------------------------- |
| GET    | `/status`       | which rung would serve, what each rung reported, outputs   |
| GET    | `/streams`      | compositor outputs (the portal reveals streams in-session) |
| POST   | `/live/start`   | open the long-lived portal session and start the stream    |
| POST   | `/live/stop`    | stop the stream and close the session                      |
| GET    | `/live/status`  | running, fps, frames seen, blank flag, last error, uptime  |
| GET    | `/frame-data`   | newest frame as a JSON data URL (polling fallback)         |
| POST   | `/capture`      | one frame through the ladder (single-shot escape hatch)    |
| GET    | `/map`          | frame pixel to logical point, or null in a dead band       |

Frames also arrive over the plugin WebSocket (`/events`) as they are
produced. `ctx.rest` stays JSON-only and `ctx.socket` is a no-op on OAuth
remotes, so the polling path is always available.

## Development

```bash
/usr/bin/python3 -m pytest -q      # runs headless, no graphical session, no portal
node --check desktop/plugin.js
```

Two interpreters are deliberate: `pytest` lives in the system Python (where
`gi` is), while `fastapi` and `uvicorn` live in the Hermes venv (where the
gateway imports `plugin_api.py`). The one test that needs a real frame is the
portal spike, which requires clicking the KDE consent dialog:

```bash
/usr/bin/python3 helpers/portal_screencast.py spike --output evidence/frame.png
/usr/bin/python3 scripts/validate_frame.py evidence/frame.png
```

## Status

Capture over the portal is working and the live viewer is wired end to end.
Input (RemoteDesktop, wlroots, XTest) and the lower capture rungs report
`capability missing` rather than pretending. See `docs/UNVERIFIED.md` for the
current list of what has and has not been proven.

## License

MIT. See `LICENSE`.
