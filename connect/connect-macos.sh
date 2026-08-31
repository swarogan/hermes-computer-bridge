#!/bin/bash
# Enable the built-in macOS Screen Sharing (VNC) and set a VNC password so the
# hermes-computer-bridge RFB client can connect. No third-party install: macOS
# ships a VNC server, this only turns it on and sets a legacy VNC password
# (standard VNC auth, which the plugin's client speaks).
#
# After running, add a VNC target in the plugin: host 127.0.0.1, port 5900,
# and the password you set below. UNTESTED by the author (developed on Linux);
# verify on your Mac. macOS 13+ may also require granting Screen Recording to
# the sharing agent in System Settings the first time.
set -euo pipefail

KICKSTART="/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart"
if [ ! -x "$KICKSTART" ]; then
  echo "kickstart not found; this script is for macOS only." >&2
  exit 1
fi

read -r -s -p "Set a VNC password (max 8 characters): " VNCPW
echo
if [ -z "$VNCPW" ] || [ "${#VNCPW}" -gt 8 ]; then
  echo "Password must be 1 to 8 characters (VNC auth limit)." >&2
  exit 1
fi

echo "Enabling Screen Sharing and setting the VNC password (needs sudo)..."
sudo "$KICKSTART" \
  -activate -configure -access -on \
  -clientopts -setvnclegacy -vnclegacy yes \
  -clientopts -setvncpw -vncpw "$VNCPW" \
  -restart -agent -privs -all

echo
echo "Done. In the plugin, Connect to new -> VNC server:"
echo "  host: 127.0.0.1"
echo "  port: 5900"
echo "  password: (the one you just set)"
