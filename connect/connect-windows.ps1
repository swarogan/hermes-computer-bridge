# Install a VNC server on Windows so the hermes-computer-bridge RFB client can
# connect. Windows has no built-in RFB server, so this installs TightVNC via
# winget. After it installs, set the control/view password in the TightVNC
# settings (its service reads that), then add a VNC target in the plugin:
# host 127.0.0.1, port 5900, and that password.
#
# Run in an ELEVATED PowerShell. UNTESTED by the author (developed on Linux);
# verify on your machine. A fully silent password setup is intentionally not
# attempted because TightVNC stores it encrypted with a fixed key and doing it
# blindly is fragile; set it once in the UI.

$ErrorActionPreference = 'Stop'

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Error "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
    exit 1
}

Write-Host "Installing TightVNC server via winget..."
winget install --id GlavSoft.TightVNC -e --silent `
    --accept-package-agreements --accept-source-agreements

Write-Host ""
Write-Host "TightVNC installed. Next steps (one time):"
Write-Host "  1. Open 'TightVNC Server - Offline Configuration' (or the tray icon)."
Write-Host "  2. Set a 'Primary password' under Server -> Authentication."
Write-Host "  3. Make sure it listens on port 5900 (default)."
Write-Host ""
Write-Host "Then in the plugin, Connect to new -> VNC server:"
Write-Host "  host: 127.0.0.1"
Write-Host "  port: 5900"
Write-Host "  password: (the primary password you set)"
