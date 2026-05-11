# lgtv-core

A small, well-tested HTTP daemon that controls an LG webOS TV from anywhere on
your local network. Speaks LG's TLS WebSocket protocol on your behalf via
[`aiowebostv`](https://github.com/home-assistant-libs/aiowebostv) (the same
library Home Assistant uses), and exposes a clean REST surface so other tools
— CLIs, bots, MCP servers — can drive the TV without re-implementing the
WebOS protocol.

This is the **core** in a three-repo set:

| Repo | Role | Lang |
|------|------|------|
| **lgtv-core** (this one) | Local HTTP daemon talking the TV's WebSocket protocol | Python |
| [`lgtv-cli`](https://github.com/deepanshutr/lgtv-cli) | Cobra CLI + Telegram bot — thin clients over `lgtv-core` | Go |
| [`lgtv-mcp`](https://github.com/deepanshutr/lgtv-mcp) | Model Context Protocol server — lets Claude control the TV as a tool | Go |

## Why a daemon?

LG TVs use a stateful TLS WebSocket on port 3001. Every consumer that wants
to send a command would otherwise need its own pairing handshake, its own
client-key storage, its own reconnect logic, and its own pointer-input
socket dance. Centralizing all of that behind one local HTTP service means
the consumers stay simple, the WebSocket state is owned by exactly one
process, and pairing happens exactly once.

## Quick start

```bash
# 1. Install
uv venv && uv pip install -e .

# 2. Configure (one-time)
cp .env.example ~/.config/lgtv/state.env
$EDITOR ~/.config/lgtv/state.env   # set LGTV_HOST and LGTV_MAC

# 3. Pair with the TV (one-time, requires you in front of the TV)
#    Make sure "Mobile TV On" is enabled in the TV's network settings first.
lgtv-core pair

# 4. Run
lgtv-core serve            # binds to 127.0.0.1:8765 by default
```

## REST API

| Method | Path | Body | Notes |
|--------|------|------|-------|
| `POST` | `/wake` | — | Sends WoL magic packet to `LGTV_MAC`. Returns when TCP 3001 accepts a connection or after 12 s timeout. |
| `POST` | `/power/off` | — | Standby. |
| `GET`  | `/state` | — | Reachability + last-seen volume / current app / current input. |
| `POST` | `/volume` | `{"level": 12}` or `{"delta": -3}` | Absolute or relative. |
| `POST` | `/mute` | `{"on": true}` | |
| `POST` | `/app/launch` | `{"id": "youtube.leanback.v4"}` | Use `GET /apps` for IDs. |
| `GET`  | `/apps` | — | App catalog from the TV (cached 60 s). |
| `POST` | `/input/switch` | `{"id": "HDMI_1"}` | |
| `GET`  | `/inputs` | — | |
| `POST` | `/key` | `{"name": "HOME"}` | Names: `HOME`, `BACK`, `MENU`, `UP`/`DOWN`/`LEFT`/`RIGHT`, `ENTER`, `EXIT`, `PLAY`, `PAUSE`, `STOP`, `RED`/`GREEN`/`YELLOW`/`BLUE`. |
| `POST` | `/pointer/move` | `{"dx": 30, "dy": -10}` | Relative mouse-pointer delta. |
| `POST` | `/pointer/click` | — | |

All endpoints return `{"ok": true, ...}` on success and a structured error
with HTTP 5xx on failure. The TV may be asleep when called — endpoints
**do not** auto-wake; call `/wake` first or use the CLI's `--auto-wake`
flag, which handles the wait loop for you.

## Configuration

Everything is env-driven. Defaults are safe for a single-TV home setup.

| Variable | Default | Meaning |
|----------|---------|---------|
| `LGTV_HOST` | _(required)_ | TV's LAN IP, e.g. `192.168.1.100` |
| `LGTV_MAC`  | _(required)_ | TV's MAC, e.g. `aa:bb:cc:dd:ee:ff` (any case, with or without colons) |
| `LGTV_BIND` | `127.0.0.1:8765` | Where the HTTP daemon listens. **Keep localhost-only** unless you front it with auth. |
| `LGTV_STATE_PATH` | `~/.config/lgtv/state.json` | Persisted client-key + last app/volume snapshot. |
| `LGTV_WAKE_TIMEOUT` | `12` | Seconds to wait for WS to come up after WoL. |
| `LGTV_LOG_LEVEL` | `INFO` | |

## Pairing flow

1. Power the TV on with the physical remote.
2. Ensure `Settings → Network → LG Connect Apps` (older firmware) or
   `Settings → General → Devices → External Devices → TV On With Mobile` (newer)
   is **enabled**. This keeps the TLS WebSocket listener alive when the
   panel is in standby.
3. Run `lgtv-core pair`. The TV will show a prompt:
   *"This application would like to access your TV."* Hit OK on the physical
   remote. The returned client-key is written to `LGTV_STATE_PATH`.

After this, the daemon will start without further prompting — even if you
factory-reset something else, the client-key is the only ceremony.

## Deployment

```bash
# install systemd user unit
cp systemd/lgtv-core.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lgtv-core
journalctl --user -u lgtv-core -f
```

## Testing

```bash
uv pip install -e .[dev]
pytest -v
```

Smoke tests run without a real TV (the WebSocket client is stubbed). Live
integration tests require `LGTV_HOST` set and `pytest -m live`.

## Hardening

- The HTTP daemon binds to `127.0.0.1` by default. Don't expose it on a LAN
  interface unless you add real auth — anyone who can reach the port can
  control the TV.
- The client-key file (`state.json`) is bearer-credential-equivalent. If
  it leaks, the attacker can drive your TV until you factory-reset.
- Lock down with: `chmod 600 ~/.config/lgtv/state.json` (the daemon does
  this on write).

## License

MIT — see `LICENSE`.
