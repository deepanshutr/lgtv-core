# Thread pickup — 2026-05-12

## What shipped this thread

Initial scaffold + 1 bug-fix release of `lgtv-core`, a local HTTP daemon
controlling an LG webOS TV via `aiowebostv`.

- `lgtv-core` Python package (FastAPI + aiowebostv + WoL)
- Operator CLI (`lgtv-core pair`, `lgtv-core serve`, `lgtv-core ping`)
- 10 pytest tests pass; ruff clean
- GitHub Actions CI (matrix py 3.11/3.12) + release pipeline + gitleaks + dependabot
- MIT licensed, public

## Live state on operator host (`deePC`)

- Daemon: systemd user unit `lgtv-core.service` — `systemctl --user status lgtv-core`
- Bound to `127.0.0.1:8765`
- Paired client-key in `~/.config/lgtv/state.json` (mode 0600, gitignored)
- TV-specific config in `~/.config/lgtv/state.env` (mode 0600, gitignored,
  contains `LGTV_HOST` + `LGTV_MAC`)

## Verified end-to-end

- `curl /state` ← TV awake → 200 with apps + volume
- `curl /state` ← TV asleep → 200 with `reachable=false` (bug-fix commit `dbe18ba`)
- `curl /wake` → WoL sent + waits for 3001
- `lgtv-core pair` against live TV — handshake succeeded, client-key persisted

## Last-known bug catalog

| # | Bug | Fix |
|---|-----|-----|
| 1 | `aiowebostv` API ≠ my assumed API — state lives in `c.tv_state`, not directly on client; pointer methods are `move`/`click` not `move_cursor`/`click_button` | Commit on initial scaffold |
| 2 | `/state` returned 502 with empty detail when TV asleep — `aiowebostv.connect()` raises bare `TimeoutError()` whose `str()` is empty | Commit `dbe18ba`: catch and return `reachable=false` snapshot |
| 3 | Python 3.10 in CI failed — `aiowebostv >= 0.7` requires Python 3.11+ | pyproject + ci.yml matrix bumped |

## Not done

- `/pointer/move` and `/pointer/click` endpoints exist but not exercised
  live yet (the WebOS pointer-input socket is a *second* WebSocket — `aiowebostv`
  handles it internally via `c.move(dx,dy)` / `c.click()` but no real-world
  test has been run)
- The `mypy` step in CI is `continue-on-error: true` — strict typing is aspirational

## Resume incantation

```bash
# Local dev
cd ~/github.com/deepanshutr/lgtv-core
source .venv/bin/activate            # uv venv if missing
pytest -v                            # 10/10 pass
ruff check lgtv_core tests           # clean

# Live operator host
systemctl --user status lgtv-core
journalctl --user -u lgtv-core -f
curl -s http://127.0.0.1:8765/state | python3 -m json.tool
```

## Related repos

- `lgtv-cli` — Go CLI + Telegram bot (standalone)
- `lgtv-mcp` — Go MCP server registered with Claude Code at user scope
- `orchctl-v2` (private) — actual Telegram bot owner, ports `/tv` handler
- `orchctl` (private, v1) — does NOT have `/tv` anymore; lgtv code lives in v2
