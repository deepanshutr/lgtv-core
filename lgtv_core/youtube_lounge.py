"""YouTube Lounge protocol — paired remote control for the TV's YouTube app.

Why this exists: LG WebOS CX-era firmware's YouTube app ignores both
`contentId` and `params.contentTarget` deep-links via the SSAP launcher
surface. The TV's YouTube app *does* honour the standard YouTube Lounge
("Cast") protocol once paired, which is the same protocol Google's Cast
SDK uses end-to-end. One-time TV-code pairing yields a `loungeToken`
that survives reboots and firmware updates — BUT despite the API
claiming `accessType: permanent` on the original screen, the token
silently expires after roughly 14 days. We refresh it via
`get_lounge_token_batch` proactively (on startup, when older than 10d)
and reactively (on a 401/410 from /bind).

Flow:
1. User pairs once: navigate on TV to YouTube → Settings → Link with TV
   code → 12-digit code. POST /youtube/pair {"pairing_code": "..."}.
2. We exchange the code for a `screen_id` + `loungeToken` via
   /api/lounge/pairing/get_screen. Save to ~/.config/lgtv/youtube_lounge.json.
3. To play any video: bind a Lounge session AND send the setPlaylist
   command in the SAME request body. The bind response sets a session
   cookie; embedding the command in the first POST avoids the 410-Gone
   second-request session-pickup problem that plagues two-step flows.
4. Tokens are refreshed automatically: at daemon startup if older than
   10 days, and on a 401/410 from /bind (which is the symptom of expiry).

State file format:
  {
    "screen_id": "<64-hex>",
    "lounge_token": "AGdO5p_...",
    "device_id": "<32-hex-upper>",      # stable per install
    "expiration": <unix-ms>,            # Google's own expiry hint
    "minted_at": <unix-s>,              # when we last got/refreshed token
    "name": "lgtv-core"
  }
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
import uuid
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Endpoint constants — never change for the public YouTube Lounge API.
YT_GET_SCREEN = "https://www.youtube.com/api/lounge/pairing/get_screen"
YT_GET_LOUNGE_TOKEN_BATCH = (
    "https://www.youtube.com/api/lounge/pairing/get_lounge_token_batch"
)
YT_BIND = "https://www.youtube.com/api/lounge/bc/bind"
YT_ORIGIN = "https://www.youtube.com"

# Refresh proactively if the token is older than this many seconds.
# Google's `loungeTokenLifespanMs` is 14d (1_209_600_000); we refresh
# well before that so a daemon that's been off for a few days doesn't
# wake up to an already-dead token.
REFRESH_AGE_S = 10 * 24 * 60 * 60  # 10 days
# Nominal lifespan reported by Google. Used purely for the /status hint
# when we haven't seen a freshly-issued token yet.
TOKEN_LIFESPAN_S = 14 * 24 * 60 * 60  # 14 days


class YoutubeLoungeError(RuntimeError):
    """Raised on any Lounge-protocol failure (auth, network, response shape)."""


def _state_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "lgtv" / "youtube_lounge.json"


def load_state() -> dict[str, Any] | None:
    p = _state_path()
    if not p.exists():
        return None
    return json.loads(p.read_text())  # type: ignore[no-any-return]


def save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True))
    p.chmod(0o600)


def _estimated_expiry_s(state: dict[str, Any]) -> int | None:
    """Best-effort guess at when the current token will stop working.

    Prefers Google's own `expiration` (unix-ms) when present; otherwise
    falls back to `minted_at + 14d`. Returns unix-seconds, or None if
    we have neither signal (legacy state file from before refresh
    support landed)."""
    exp_ms = state.get("expiration")
    if isinstance(exp_ms, int) and exp_ms > 0:
        return exp_ms // 1000
    minted = state.get("minted_at")
    if isinstance(minted, int) and minted > 0:
        return minted + TOKEN_LIFESPAN_S
    return None


async def pair_with_code(pairing_code: str) -> dict[str, Any]:
    """Exchange a 12-digit TV code for a paired Lounge session.

    The pairing_code comes from the TV's YouTube app:
    YouTube → Settings → Link with TV code. Single-use; reuse the
    stored loungeToken for subsequent operations (auto-refreshed
    every ~10 days, see refresh_token())."""
    digits = "".join(c for c in pairing_code if c.isdigit())
    if len(digits) != 12:
        raise YoutubeLoungeError(
            f"pairing_code must be 12 digits, got {len(digits)}: {pairing_code!r}"
        )
    async with httpx.AsyncClient(
        headers={"Origin": YT_ORIGIN}, timeout=15
    ) as client:
        r = await client.post(YT_GET_SCREEN, data={"pairing_code": digits})
        if r.status_code != 200:
            raise YoutubeLoungeError(
                f"get_screen failed: HTTP {r.status_code} — "
                "code expired, was used, or wasn't typed correctly?"
            )
        screen = r.json()["screen"]
    state = {
        "screen_id": screen["screenId"],
        "lounge_token": screen["loungeToken"],
        "device_id": uuid.uuid4().hex.upper(),
        "expiration": screen.get("expiration"),
        "name": screen.get("name", "lgtv-core"),
        "minted_at": int(time.time()),
    }
    save_state(state)
    log.info("YouTube Lounge paired (device_id=%s)", state["device_id"])
    return {
        "paired": True,
        "screen_id": state["screen_id"],
        "device_id": state["device_id"],
        "expiration": state["expiration"],
    }


async def refresh_token() -> dict[str, Any]:
    """Refresh the cached loungeToken via get_lounge_token_batch.

    Uses the existing screen_id (which is permanent and survives across
    refreshes — only the loungeToken on top of it expires). Returns the
    refresh result with the new token + expiry; raises YoutubeLoungeError
    if we're not paired or Google rejected the screen_id.

    A 404 / empty `screens[]` here means the screen_id itself has been
    invalidated upstream (TV un-linked, account changes) — at that
    point only a fresh 12-digit pairing will recover."""
    state = load_state()
    if not state:
        raise YoutubeLoungeError(
            "not paired — call /youtube/pair with a TV code first"
        )
    screen_id = state["screen_id"]
    async with httpx.AsyncClient(
        headers={"Origin": YT_ORIGIN}, timeout=15
    ) as client:
        r = await client.post(
            YT_GET_LOUNGE_TOKEN_BATCH,
            data={"screen_ids": screen_id},
        )
        if r.status_code != 200:
            raise YoutubeLoungeError(
                f"get_lounge_token_batch failed: HTTP {r.status_code} — "
                f"{r.text[:200]}"
            )
        body = r.json()
    screens = body.get("screens") or []
    # Google returns the screen entry only if the id is still valid.
    # A missing entry == fully invalidated screen, needs re-pair.
    match = next(
        (s for s in screens if s.get("screenId") == screen_id),
        None,
    )
    if not match or "loungeToken" not in match:
        raise YoutubeLoungeError(
            "screen_id no longer valid upstream — TV must be re-paired "
            "via /youtube/pair with a fresh 12-digit code"
        )
    state["lounge_token"] = match["loungeToken"]
    if "expiration" in match:
        state["expiration"] = match["expiration"]
    state["minted_at"] = int(time.time())
    save_state(state)
    log.info(
        "YouTube Lounge token refreshed (expiration=%s)", state.get("expiration")
    )
    return {
        "refreshed": True,
        "screen_id": screen_id,
        "expiration": state.get("expiration"),
        "minted_at": state["minted_at"],
    }


async def refresh_if_stale() -> bool:
    """Refresh the token iff older than REFRESH_AGE_S. Safe to call at
    startup. Returns True if a refresh happened, False otherwise (no
    pairing, or token is still fresh). Never raises — startup paths
    shouldn't crash the daemon over a YouTube hiccup."""
    state = load_state()
    if not state:
        log.debug("refresh_if_stale: not paired, skipping")
        return False
    minted = state.get("minted_at")
    # Legacy state files without minted_at are treated as old and
    # refreshed once on first boot to backfill the field.
    age_s: float
    if not isinstance(minted, int) or minted <= 0:
        age_s = float("inf")
    else:
        age_s = time.time() - minted
    if age_s < REFRESH_AGE_S:
        log.debug("refresh_if_stale: token age %.1fd, still fresh", age_s / 86400)
        return False
    try:
        await refresh_token()
        return True
    except Exception as e:
        log.warning(
            "refresh_if_stale: refresh failed (%s); existing token will be "
            "tried — re-pair via /youtube/pair if /youtube/play returns auth errors",
            e,
        )
        return False


async def play_video(video_id: str, start_time_s: int = 0) -> None:
    """Play a YouTube video on the paired TV. Requires prior pairing.

    Uses the single-shot bind-with-command pattern: the setPlaylist command
    is embedded in the initial bind POST body. The bind response sets the
    session cookie, and embedding the command in the same RPC avoids the
    two-request 410-Gone trap (where the SID returned from bind#1 isn't
    yet usable from bind#2 without proper cookie/sticky-routing handling).

    Auto-refreshes the loungeToken on a 401/410 response (the signature
    of an expired token) and retries the bind once before giving up.
    """
    if not load_state():
        raise YoutubeLoungeError(
            "not paired — call /youtube/pair with a TV code first"
        )

    async def _attempt() -> httpx.Response:
        # Re-load state each attempt so a refresh-mid-flight is picked up.
        st = load_state()
        assert st is not None  # checked by caller
        params = {
            "device": "REMOTE_CONTROL",
            "id": st["device_id"],
            "name": st.get("name") or "lgtv-core",
            "app": "youtube-desktop",
            "loungeIdToken": st["lounge_token"],
            "VER": "8",
            "CVER": "1",
            "RID": "1",
            "zx": uuid.uuid4().hex[:12],
        }
        body = {
            "count": "1",
            "ofs": "0",
            "req0__sc": "setPlaylist",
            "req0_videoId": video_id,
            "req0_listId": "",
            "req0_currentIndex": "0",
            "req0_currentTime": str(start_time_s),
            "req0_audioOnly": "false",
        }
        async with httpx.AsyncClient(
            headers={"Origin": YT_ORIGIN}, timeout=15
        ) as client:
            return await client.post(YT_BIND, params=params, data=body)

    r = await _attempt()
    # 401 = explicit auth reject. 410 = stale SID, which on a fresh
    # bind#1 means the loungeToken itself is no longer accepted (the
    # SID lives in the response, not the request, so it can't be us).
    if r.status_code in (401, 410):
        log.info(
            "play_video: HTTP %s from /bind — refreshing loungeToken and retrying",
            r.status_code,
        )
        await refresh_token()
        r = await _attempt()
        if r.status_code in (401, 410):
            raise YoutubeLoungeError(
                f"Lounge token still rejected (HTTP {r.status_code}) after "
                "refresh — re-pair via /youtube/pair with a fresh TV code"
            )
    if r.status_code != 200:
        raise YoutubeLoungeError(
            f"setPlaylist failed: HTTP {r.status_code} — {r.text[:200]}"
        )


def status() -> dict[str, Any]:
    """Report whether we're paired and how close the token is to expiry.

    Synchronous because it only touches the local state file. Includes
    `minted_at` and `estimated_expiry` (unix-s) so callers can render
    a human-readable age without re-implementing the math.
    """
    state = load_state()
    if not state:
        return {"paired": False}
    minted = state.get("minted_at")
    expiry_s = _estimated_expiry_s(state)
    now = int(time.time())
    out: dict[str, Any] = {
        "paired": True,
        "screen_id": state.get("screen_id"),
        "device_id": state.get("device_id"),
        "expiration": state.get("expiration"),
        "name": state.get("name"),
        "minted_at": minted,
        "estimated_expiry_s": expiry_s,
    }
    if isinstance(minted, int) and minted > 0:
        out["age_s"] = now - minted
    if expiry_s is not None:
        out["seconds_until_expiry"] = expiry_s - now
    return out
