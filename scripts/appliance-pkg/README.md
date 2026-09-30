# Anthill Appliance package

A double-click macOS installer (`.pkg`) that turns this Mac into the always-on Anthill org
backend - the polished version of `anthill appliance install`.

It is a **scripts-only** package (no app payload): install the Anthill app first, then run
this to make it always-on. On install, the root `postinstall`:

1. applies the always-on power settings (`pmset`: never sleep, restart after a power cut,
   wake for network), then
2. installs the per-user LaunchAgent and pulls the sized model **as the logged-in user** -
   the agent must live in that user's GUI session so Ollama gets the Apple GPU (Metal is
   unavailable to a pre-login system daemon).

## Build

```bash
bash scripts/build-appliance-pkg.sh            # unsigned -> dist/Anthill-Appliance.pkg
```

Unsigned is fine for a Mac you control in your office (the installer just asks the admin to
allow it). To distribute it more widely, sign with a Developer ID:

```bash
INSTALLER_ID="Developer ID Installer: Your Org (TEAMID)" bash scripts/build-appliance-pkg.sh
```

## After install

Enable **auto-login** for the user it runs as (System Settings -> Users & Groups -> Login
Options) so the GPU stays available after a reboot, then open **Settings -> Appliance** for
the LAN URL and the Metal-vs-CPU status. FileVault blocks unattended auto-login - leave it off
(physical security) or set up `fdesetup` automatic unlock.
