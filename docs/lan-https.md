# LAN HTTPS deployment

Target: `https://glyph.local:4318/`. Agent Office must run only at `127.0.0.1:4319`; Caddy owns the LAN-facing TLS port 4318 and uses its existing `tls internal` CA.

## Cutover

1. Save `/opt/homebrew/etc/Caddyfile` with a timestamp and validate the saved file with `caddy validate --config /opt/homebrew/etc/Caddyfile`.
2. Copy `deploy/com.mcornelia.agent-office.server.plist`, replace `REPOSITORY_PATH`, `CODEX_PATH`, `SUPPORT_PATH`, and `LOG_PATH`, and save it as `~/Library/LaunchAgents/com.mcornelia.agent-office.server.plist`. Create the support/log directories with owner-only permissions, validate the plist with `plutil -lint`, then load it with the current user's `launchctl bootstrap gui/$(id -u)`.
3. Add the contents of `deploy/Caddyfile.agent-office-4318` to the live Caddyfile. Validate, then reload Caddy. Do not alter the existing `glyph.local, openclaw-mac-mini.local` block.
4. Verify `lsof -nP -iTCP:4319 -sTCP:LISTEN` shows only `127.0.0.1`; verify Caddy owns `*:4318`; then open `https://glyph.local:4318/` from a client that trusts Caddy's internal root. For command-line verification, pass that root certificate's actual path to `curl --cacert` rather than disabling certificate checks.

## Rollback

Unload `com.mcornelia.agent-office.server`, remove only the dedicated `https://glyph.local:4318` block, restore the saved Caddyfile, validate, and reload. The existing glyph.local 443 routes remain untouched. The desktop launcher's optional login item uses the separate label `com.mcornelia.agent-office.launcher`, so the two services cannot overwrite one another.

Clients other than the Mac mini must trust Caddy's internal root CA before their browser will accept this HTTPS endpoint.

To build the optional desktop app for this layout, point its embedded local view at the backend while also allowing it to restart that backend safely if the server LaunchAgent is unavailable:

```sh
AGENT_OFFICE_BACKEND_PORT=4319 AGENT_OFFICE_PUBLIC_HOST=glyph.local:4318 ./desktop/install.sh
```

This does not enable the app's **Start at Login** option. The always-on server and the optional menu-bar launcher are deliberately separate.
