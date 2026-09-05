# LAN HTTPS deployment

Target: `https://glyph.local:4318/`. Agent Office must run only at `127.0.0.1:4319`; Caddy owns the LAN-facing TLS port 4318 and uses its existing `tls internal` CA.

## Cutover

1. Save `/opt/homebrew/etc/Caddyfile` with a timestamp and validate the saved file with `caddy validate --config /opt/homebrew/etc/Caddyfile`.
2. Install `deploy/com.mcornelia.agent-office.plist` after replacing `REPOSITORY_PATH` and `LOG_PATH`; load it with the current user's `launchctl bootstrap gui/$(id -u)`.
3. Add the contents of `deploy/Caddyfile.agent-office-4318` to the live Caddyfile. Validate, then reload Caddy. Do not alter the existing `glyph.local, openclaw-mac-mini.local` block.
4. Verify `lsof -nP -iTCP:4319 -sTCP:LISTEN` shows only `127.0.0.1`; verify Caddy owns `*:4318`; use `curl --cacert "$(caddy trust --help >/dev/null 2>&1; echo /path/to/caddy/root.crt)" https://glyph.local:4318/` or a client that trusts Caddy's internal root.

## Rollback

Unload the LaunchAgent, remove only the dedicated `https://glyph.local:4318` block, restore the saved Caddyfile, validate, and reload. The existing glyph.local 443 routes remain untouched.

Clients other than the Mac mini must trust Caddy's internal root CA before their browser will accept this HTTPS endpoint.
