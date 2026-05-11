# Thread pickup — 2026-05-11 (lgtv-core)

## What shipped this session

Initial scaffolding of the FastAPI + aiowebostv HTTP daemon. Three commits:

- `459e3ce` Initial scaffolding (FastAPI app, aiowebostv client, systemd
  unit, gitleaks CI, dependabot, MIT LICENSE, README, CLAUDE.md).
- `17b9cde` Python floor bumped 3.10 → 3.11 (aiowebostv requirement; CI
  failed on 3.10 first).
- `094547c` Fixed real aiowebostv API mismatch: state is `c.tv_state:
  WebOsTvState` (a dataclass), not attrs on the client. Pointer methods
  are `c.move(dx,dy)` / `c.click()`, not `move_cursor` / `click_button`.

## Live state

- **Paired with the TV.** Client-key persisted to `~/.config/lgtv/state.json`
  (mode 0600, gitignored). Fingerprint `c1f222b8…17a2`.
- Daemon runs as **systemd user unit** `lgtv-core.service`. Binds
  `127.0.0.1:8765`. Restart: `systemctl --user restart lgtv-core`.
- Binary symlinked at `~/.local/bin/lgtv-core` → venv at
  `~/github.com/deepanshutr/lgtv-core/.venv/bin/lgtv-core`.
- TV: LG OLED55CXPTA at `192.168.1.12`, MAC `95:34:8e:1c:a7:63`. Local
  hostname `LGwebOSTV.local`.

## Local-only config (never commit)

`~/.config/lgtv/state.env`:
```
LGTV_HOST=192.168.1.12
LGTV_MAC=95:34:8e:1c:a7:63
LGTV_BIND=127.0.0.1:8765
LGTV_LOG_LEVEL=INFO
```

## Not yet wired

- Pointer/click endpoints exist on the daemon side but the TV needs a
  separate `getPointerInputSocket` WebSocket. aiowebostv handles that
  internally — just call `c.move(dx,dy)` / `c.click()`. End-to-end never
  exercised.

## Gotchas to NOT re-discover

1. **"Mobile TV On"** must be enabled
   (`Settings → General → Devices → External Devices → TV On With Mobile`)
   or TCP 3001 sleeps in standby and every request hangs forever. mDNS/SSDP
   still answer in this state, which is misleading.
2. **aiowebostv requires Python ≥ 3.11.**
3. Local Go env: `~/.profile` exports `GOROOT=/home/deepanshutr/go/go1.18`
   which mismatches the brew go at `/home/linuxbrew/.linuxbrew/bin/go`
   (1.26.2). Always `unset GOROOT; export
   GOPROXY=https://proxy.golang.org,direct` before any `go` command —
   relevant only when working on lgtv-cli / lgtv-mcp from here.
4. **Push email must be the noreply** for this account:
   `52166434+deepanshutr@users.noreply.github.com`. Set per-repo via
   `git config user.email`. Do not touch global gitconfig.

## Exact resume incantation

```bash
cd ~/github.com/deepanshutr/lgtv-core
source .venv/bin/activate
pytest -q                       # all green at HEAD 094547c
systemctl --user status lgtv-core
journalctl --user -u lgtv-core -f --since "5 min ago"

# Quick smoke test:
curl -s http://127.0.0.1:8765/state | jq .
curl -s -X POST http://127.0.0.1:8765/volume -H 'content-type: application/json' -d '{"delta":-1}' | jq .
```

## Repo state at thread-close

- Branch: `main`, up to date with `origin/main`, clean tree.
- Three commits since init (see above).
- CI green; gitleaks green; dependabot wired.

## Memory references

- `project_lgtv_stack.md` — full project / sibling-repo overview
