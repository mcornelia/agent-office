# Agent Office for macOS

The desktop prototype is a small native AppKit menu-bar application with an embedded WebKit window. Its thin wrapper is compiled directly from Objective-C so the build does not depend on a Swift compiler/SDK version match. It starts the existing loopback-only Python office server when needed, opens a normal office window, and offers a one-click fullscreen presentation view.

Nothing in this directory installs itself. Start at Login is off by default and is enabled only through the menu item or the explicit installer flag.

## Why this packaging

| Option | Advantages | Tradeoffs |
| --- | --- | --- |
| **AppKit + WebKit status app (chosen)** | Native menu bar and fullscreen behavior; reuses the existing UI and Python service; no Electron runtime; presentation stays on loopback | Requires Xcode Command Line Tools to build; redistribution needs Developer ID signing and notarization |
| AppleScript app + default browser | Very small and easy to inspect | No durable menu-bar home; browser fullscreen and window reuse are unreliable |
| `SMAppService` login helper | Apple's modern managed Login Items UI | Requires an embedded helper, entitlements, signing, and a more complex Xcode project |
| Electron or Tauri | Rich packaging ecosystem | Adds a substantial runtime or Rust/toolchain dependency for a local HTML view |

The prototype uses an opt-in per-user LaunchAgent plist for Start at Login. That is less integrated than `SMAppService`, but it is transparent, reversible, and practical for an unsigned local build. A distributable release should move login management to `SMAppService` after the app has a stable signing identity.

## Runtime behavior

- The controller checks its configured loopback port (4318 by default) before starting anything.
- A file lock serializes concurrent starts. A healthy existing Agent Office is reused, and a different service on the configured port causes a safe failure.
- The launcher records only processes it started. **Stop Local Server** refuses to terminate a process that does not match that ownership record.
- Private runtime files and logs live in `~/Library/Application Support/Agent Office` with owner-only permissions.
- The menu-bar app remains available when its office window is closed. Quitting the app leaves the local server running; use **Stop Local Server** when desired.

## Presentation privacy

The presentation window loads `/presentation` and enters native macOS fullscreen. Its polling requests `/api/state?presentation=1`.

The server removes task IDs, task titles, roles, local error paths, and manager communication IDs before serializing that response. Occupied desks are labeled only `Agent 1` through `Agent 6`, and communication bubbles are suppressed. CSS also removes the assignment/selection panel for a clean stage view, but privacy does not depend on CSS because the sensitive fields are absent from the response.

## Build without installing

Requirements: macOS 13 or newer, `/usr/bin/python3`, and Xcode Command Line Tools.

```sh
./desktop/build_app.sh
open "desktop/build/Agent Office.app"
```

The build is ad-hoc signed by default. To use an installed Developer ID identity:

```sh
AGENT_OFFICE_SIGNING_IDENTITY="Developer ID Application: Example (TEAMID)" ./desktop/build_app.sh
```

The app bundle contains the server runtime. It also records this checkout so an existing ignored `manager/team.json` can continue to drive generic manager activity bubbles. If the checkout moves, rebuild the app; ordinary desk status continues to work from the bundled runtime.

## Install later

Install to the user's Applications directory, without enabling login startup:

```sh
./desktop/install.sh
open "$HOME/Applications/Agent Office.app"
```

Enable Start at Login only when intentionally requested:

```sh
./desktop/install.sh --enable-login
```

Alternatively, toggle **Start at Login** from the menu after installation. The plist is `~/Library/LaunchAgents/com.mcornelia.agent-office.launcher.plist`; it opens the status app in background login mode and does not keep-restart a failing process. This label is separate from the optional always-on LAN server.

The installer refuses to overwrite an existing application. Uninstall or move that copy before installing a replacement.

For a Caddy LAN deployment that exposes `https://glyph.local:4318` while keeping the Python backend on loopback port 4319, build the local app with:

```sh
AGENT_OFFICE_BACKEND_PORT=4319 AGENT_OFFICE_PUBLIC_HOST=glyph.local:4318 ./desktop/install.sh
```

See [LAN HTTPS deployment](../docs/lan-https.md). Start at Login remains off unless you add `--enable-login` or toggle it in the menu.

If the repository or manager ledger is in Dropbox, iCloud Drive, or another macOS File Provider path, prefer this desktop-launcher method. A bare Python LaunchAgent can be denied access to those paths even though the interactive app works normally; the LAN guide explains the safe alternatives.

## Uninstall later

```sh
./desktop/uninstall.sh
```

This disables Start at Login, asks the controller to stop only its owned server, and moves the app to the Trash. It deliberately retains private runtime data and logs in `~/Library/Application Support/Agent Office`; review and remove that directory separately if desired.

## Tests

```sh
python3 -m unittest -v test_server.py desktop/test_agent_office_ctl.py
python3 -m unittest -v test_desktop_status.py test_communications.py
python3 -m unittest discover -s manager -p 'test_*.py' -v
./desktop/build_app.sh
```

No test enables a real login item. The controller tests use temporary directories and a mocked loopback health response.

## Release blockers

- The default build is ad-hoc signed. Sharing outside the local Mac requires a Developer ID Application certificate, hardened runtime validation, notarization, and stapling.
- The app uses the system Python at `/usr/bin/python3`, matching the current Agent Office runtime. A consumer distribution should bundle or replace that runtime to avoid depending on the host's Python availability.
- The status feed and Codex database are observed internal formats; packaging does not remove that compatibility constraint.
