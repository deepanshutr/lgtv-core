# Thread Pickup — 2026-05-13

State of the repo when this session ended. New contributors / future-Claude:
start here before touching anything.

## Where the daemon is now

Built up over today's session into a much more capable TV-control daemon.
What's new since 2026-05-12 pickup:

### Bug fixes
- **WoL was broken** — `send_wol` only targeted `255.255.255.255:9` which
  most consumer routers drop inside the LAN. Fixed by sending to subnet
  broadcast + unicast + global. Plus `wake()` now raises on timeout
  instead of returning a `bool: False` that `_wrap` silently turned into
  HTTP 200. (Commit 47f03e9)
- **`_CONNECT_TIMEOUT_S` was too tight** at 2.5s. Cold post-wake handshake
  needs 3-5s on the CX firmware. Bumped to 5.0s. (Part of c231ad3)

### New features
- **SSDP rediscovery** — daemon auto-heals when the TV's DHCP lease rolls.
  `_ensure_client` falls back to SSDP M-SEARCH on connect failure, persists
  the new IP to `state.json:last_known_host`. (Commit ac1ffbe)
- **Perf optimizations** — eager-connect at startup (background task, not
  in lifespan blocking path), tighter `wait_for_port` poll cadence, SSDP
  burst with shorter MX. `state_cold` halved (4.5s → 2.2s); `ssdp_rediscover`
  cut 26%. (Commit 5441a2b)
- **Deep-link params** — `/app/launch` accepts `content_id` and `params`
  for app-specific deep-linking. (Commit a973b44)
- **post_keys automation** — `/app/launch` accepts `post_launch_delay_ms`
  and `post_keys` array. Bundles "launch + dismiss profile/welcome + play"
  into one daemon call. (Commit f73e217)
- **Sound output endpoint** — `POST /sound_output {"output": "external_arc"}`.
  Wraps `change_sound_output`. Note: switching to `external_arc` resets
  the TV's internal volume to ~15 (CEC handoff), so re-issue `/volume`
  after the switch if you need a specific level. (Commit 8056ef7)
- **Playback introspection** — `GET /playback` returns
  `{media_foreground, audio_status, current_app_id}`. Each field probed
  independently; CX firmware doesn't support `com.webos.media/getForegroundAppInfo`
  (returns 404), so we surface that per-field rather than failing the
  whole endpoint. (Commit a454600)
- **/app/close endpoint** — `POST /app/close {"id": "..."}` calls
  `close_app`. Note: LG TVs return 403 if you try to close an app from
  its idle home screen state. EXIT key is a more reliable close. (Part
  of c231ad3)
- **YouTube Lounge protocol** — **the big one**. Full paired Cast-style
  remote control. (Commit c231ad3)

## YouTube Lounge — read this if you're touching `youtube_lounge.py`

The journey: spent half a session trying every documented `launch_app`
deep-link variation to make YouTube play a specific video on CX firmware.
Nothing reliably worked — see the matrix below. Pivoted to the Lounge
protocol (the same protocol Google Cast uses) and that solved it.

### Launch-payload matrix (all FAIL on CX YouTube)
| Payload | Result |
|---|---|
| `{contentId: "ID"}` (top-level) | profile picker eats it, lands on first recommended |
| `{params: {contentTarget: "https://www.youtube.com/watch?v=ID"}}` | YouTube home |
| `{params: {contentTarget: "https://www.youtube.com/tv?v=ID&autoplay=1"}}` | YouTube home |
| `{params: {contentTarget: "tv#/watch?v=ID&action=play"}}` | YouTube home |
| `{params: {contentId: "ID"}}` (NESTED) | works ONCE when user manually clicks profile; not reproducible with daemon-driven ENTER |
| Two-stage: plain launch → re-launch with params while profile up | first recommended |
| Three-stage: launch params → ENTER → re-launch params | first recommended |
| Re-launch with various DOWN/LEFT/pointer click variations | first recommended / shorts |

### What actually works: Lounge protocol (`c231ad3`)

```
1. User pairs once (TV-side):
   YouTube → Settings → Link with TV code → see 12 digits.

2. POST /youtube/pair {"pairing_code": "893949732447"}
   Daemon hits https://www.youtube.com/api/lounge/pairing/get_screen.
   Gets back: screen_id + permanent loungeToken + deviceId.
   Saves to ~/.config/lgtv/youtube_lounge.json (mode 0600).

3. POST /youtube/play {"video_id": "jNQXAC9IVRw"}
   Daemon binds the Lounge session AND sends setPlaylist
   in the SAME POST body (this is the critical detail —
   see "the 410-Gone trap" below).
```

### The 410-Gone trap

Lounge protocol naturally feels like a two-step flow:
1. POST `/api/lounge/bc/bind` with `count=0` → get SID + gsessionid
2. POST `/api/lounge/bc/bind?SID=…&gsessionid=…` with `count=1`,
   `req0__sc=setPlaylist&req0_videoId=…`

**Step 2 always returns 410 Gone.** The bind response sets a session
cookie (`set-cookie: S=youtube_lounge_remote=<SID>`) but cookie
installation races the second request. Even when we wait + include
the cookie manually, GFE sticky-routing decides our request belongs
to a different shard than the session.

**Fix:** embed the command in the initial bind body. One POST,
session created + command executed atomically. That's what
`play_video` does in `youtube_lounge.py`.

### Identity gate

Without pairing, the bind succeeds but every command returns 410 because
the bind response includes `receiverIdentityMatchStatus: DOES_NOT_MATCH_RECEIVER`.
Google requires the remote to share an account identity with the TV's
YouTube login before write operations are accepted. This is intentional
security — pre-2018 anyone on the LAN could hijack your TV.

The TV-code pairing flow is the official escape hatch: user explicitly
authorizes our remote, we get a `permanent` loungeToken.

## State files

- `~/.config/lgtv/state.env` — env-driven config (LGTV_HOST, LGTV_MAC).
  Don't write to this from the daemon; it's read-only there.
- `~/.config/lgtv/state.json` — `client_key` (WebSocket pairing) +
  `last_known_host` (SSDP-rediscovered IP). Daemon owns this.
- `~/.config/lgtv/youtube_lounge.json` — `screen_id`, `lounge_token`,
  `device_id`. Mode 0600. The loungeToken is bearer-credential-equivalent
  — never commit, never log it in full.

## Open gotchas / things future-Claude will hit

1. **YouTube Lounge token expiry.** `accessType: permanent` doesn't mean
   forever — it means ~14 days, with a refresh mechanism we haven't
   implemented yet. When the token expires, `/youtube/play` will start
   returning 401 from the bind URL. The user will need to re-pair.
   A background refresh task should be added.
2. **TV's profile state.** YouTube Lounge plays under whichever profile
   the TV currently has active. If the TV has been idle long enough to
   re-show the profile picker, the first `play_video` call may not work
   until profile is selected (Lounge protocol may or may not auto-pass
   profile — needs testing).
3. **lgtv-mcp uses an older binary** in `~/.local/bin/lgtv-mcp`. The
   stdio MCP server reads its tool schema at session-start; the bumped
   tool with `content_id` / `content_target` won't be available until
   the user starts a new Claude session. (Same applies to any future
   tool additions for `/youtube/play` etc.)
4. **`/app/close` 403** — LG TVs reject `close_app` SSAP calls when the
   target app is on its idle home screen. Use the EXIT key instead, or
   bounce via `/app/launch {"id": "com.webos.app.livetv"}`.
5. **Sound-output volume reset** — switching to `external_arc` resets
   the TV's internal volume. Order is: `/sound_output` first, then
   `/volume` to the desired level.
6. **CX media-foreground 404** — `com.webos.media/getForegroundAppInfo`
   is a newer-firmware endpoint. CX returns 404. Don't expect
   `/playback.media_foreground` to populate on this set.

## Next steps the user mentioned wanting

- Add `tv_youtube_play` and `tv_youtube_pair` to lgtv-mcp (next session
  is the right place; older MCP binary is still running).
- Background loungeToken refresh task in the daemon (before token
  expiry).
- Lounge `pause`/`resume`/`stop`/`seek`/`next`/`prev` commands — same
  bind+command pattern as `play_video`, different `req0__sc` values.
- Queue management: `addToQueue`, `clearQueue`, etc. via Lounge.

## Verified working as of session end
- Power off / wake (cold standby → reachable in 16-17s, warm in 1.8s).
- SSDP rediscovery (state.json `last_known_host` auto-heals from 192.168.1.99 → 192.168.1.13).
- `/app/launch` with `post_keys` (launch + auto-dismiss flow).
- `/sound_output external_arc` (HDMI ARC routing).
- `/youtube/pair` (with TV code 893949732447).
- `/youtube/play` (played "Me at the zoo" `jNQXAC9IVRw`, then "Bailamos" `5sye_VxmNZA`).
- Pause via `/key PAUSE`.
