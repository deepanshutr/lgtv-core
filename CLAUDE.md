# CLAUDE.md — lgtv-core

Context for future Claude sessions working in this repo.

## What this is

A local HTTP daemon that translates REST calls into LG webOS WebSocket
commands. Acts as the single owner of the TV's WebSocket session so that
multiple consumers (CLI, Telegram bot, MCP server) don't fight over it.

## Architecture

```
HTTP client ──▶ FastAPI (lgtv_core.main)
                   │
                   ▼
               TVDriver (lgtv_core.tv)
                   │
                   ▼
              aiowebostv.WebOsClient  ◀──┐
                   │                     │ persistent
                   ▼                     │ TLS WebSocket
                LG TV ──────────────────┘
```

- `lgtv_core.main` — FastAPI app, routes, lifespan that owns the TVDriver.
- `lgtv_core.tv` — the WebOS wrapper. Single `aiowebostv.WebOsClient`
  instance per process, lazy-connects on first command, reconnects on
  `ConnectionClosed`.
- `lgtv_core.wol` — Wake-on-LAN magic-packet sender + TCP-3001 poll loop.
- `lgtv_core.state` — atomic JSON read/write of the client-key + last-known
  TV snapshot. Mode 0600.
- `lgtv_core.config` — pydantic-settings BaseSettings, env-driven.

## Design rules

1. **No TV identifiers in the repo.** IP, MAC, serial, hostname all come
   from env vars. `.env.example` has placeholders only.
2. **Daemon binds localhost-only by default.** Any change to that needs
   explicit auth in front of it.
3. **One WebSocket session per process.** If you find yourself opening a
   second one, you've drifted — fix the root cause instead.
4. **Pairing requires user interaction.** Never try to "auto-pair" or
   suppress the on-screen prompt. The prompt is the whole security model.
5. **Endpoints don't auto-wake.** Clients call `/wake` explicitly or use
   `--auto-wake` on the CLI side. Auto-wake on every endpoint hides bugs.

## Gotchas (the ones future-you will hit)

- `aiowebostv` requires Python ≥ 3.10. We pin 3.12 in CI for stability.
- TV's TLS cert is self-signed and rotates with firmware updates. The
  client disables verification (`ssl_context.verify_mode = CERT_NONE`)
  — that's the Home Assistant pattern, fine for LAN-only.
- The **pointer input socket** (mouse / keyboard input) is a *second*
  WebSocket the TV hands you via `ssap://com.webos.service.networkinput/getPointerInputSocket`.
  It speaks a different (line-oriented) protocol. `aiowebostv` handles
  this but if you ever bypass it, don't be surprised.
- After firmware updates the TV may forget pairings. Re-run `lgtv-core pair`.
- LG CX-line "Mobile TV On" must be enabled in the TV's settings — when off,
  the TCP listener sleeps in standby and every request times out. The TV
  still answers SSDP/mDNS in this state, which is *misleading* — being
  discoverable doesn't mean being reachable.
- The `system/turnOff` command returns success but the WebSocket then
  closes — handle the disconnect, don't treat it as an error.

## Adding new endpoints

1. Add the method to `TVDriver` in `tv.py`. It should be `async`, do nothing
   but call into `aiowebostv`, and re-raise its exceptions.
2. Add a route in `main.py` that calls the driver. Validate the body with
   a pydantic model.
3. Add a smoke test in `tests/test_smoke.py` that stubs the driver and
   checks the route returns 200.

## Testing without a TV

```bash
pytest                  # stubbed, fast, runs in CI
pytest -m live          # requires real TV + LGTV_HOST + paired state
```

The stub lives in `tests/conftest.py` and mocks `aiowebostv.WebOsClient`.

## Running locally

```bash
uv venv && uv pip install -e .[dev]
export LGTV_HOST=192.168.1.x LGTV_MAC=aa:bb:cc:dd:ee:ff
uvicorn lgtv_core.main:app --reload --port 8765
```

## Where state lives

- `~/.config/lgtv/state.json` — client-key + last-seen TV state. Never
  committed.
- `~/.config/systemd/user/lgtv-core.service` — systemd unit (template in
  `systemd/`).
- Logs: `journalctl --user -u lgtv-core -f`.

## CI/CD

- `.github/workflows/ci.yml` — ruff, mypy, pytest on every PR.
- `.github/workflows/release.yml` — on tag, builds wheel + sdist, uploads
  as release artifact.
- `gitleaks` runs on every push to catch accidental secrets.

## Related repos

- `lgtv-cli` — Go Cobra CLI + Telegram bot that hits this daemon's HTTP API.
- `lgtv-mcp` — Model Context Protocol server, lets Claude call this
  daemon's endpoints as MCP tools.
