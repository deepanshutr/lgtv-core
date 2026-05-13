"""YouTube Lounge protocol — paired remote control for the TV's YouTube app.

Why this exists: LG WebOS CX-era firmware's YouTube app ignores both
`contentId` and `params.contentTarget` deep-links via the SSAP launcher
surface. The TV's YouTube app *does* honour the standard YouTube Lounge
("Cast") protocol once paired, which is the same protocol Google's Cast
SDK uses end-to-end. One-time TV-code pairing yields a permanent
loungeToken that survives reboots and firmware updates.

Flow:
1. User pairs once: navigate on TV to YouTube → Settings → Link with TV
   code → 12-digit code. POST /youtube/pair {"pairing_code": "..."}.
2. We exchange the code for a `screen_id` + permanent `loungeToken` via
   /api/lounge/pairing/get_screen. Save to ~/.config/lgtv/youtube_lounge.json.
3. To play any video: bind a Lounge session AND send the setPlaylist
   command in the SAME request body. The bind response sets a session
   cookie; embedding the command in the first POST avoids the 410-Gone
   second-request session-pickup problem that plagues two-step flows.

State file format:
  {
    "screen_id": "<64-hex>",
    "lounge_token": "AGdO5p_...",
    "device_id": "<32-hex-upper>",      # stable per install
    "expiration": "<unix-ms>"
  }
"""

from __future__ import annotations

import json
import logging
import pathlib
import uuid
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Endpoint constants — never change for the public YouTube Lounge API.
YT_GET_SCREEN = "https://www.youtube.com/api/lounge/pairing/get_screen"
YT_BIND = "https://www.youtube.com/api/lounge/bc/bind"
YT_ORIGIN = "https://www.youtube.com"


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


async def pair_with_code(pairing_code: str) -> dict[str, Any]:
    """Exchange a 12-digit TV code for a permanent paired Lounge session.

    The pairing_code comes from the TV's YouTube app:
    YouTube → Settings → Link with TV code. Single-use; reuse the
    stored loungeToken for subsequent operations.
    """
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
    }
    save_state(state)
    log.info("YouTube Lounge paired (device_id=%s)", state["device_id"])
    return {
        "paired": True,
        "screen_id": state["screen_id"],
        "device_id": state["device_id"],
        "expiration": state["expiration"],
    }


async def play_video(video_id: str, start_time_s: int = 0) -> None:
    """Play a YouTube video on the paired TV. Requires prior pairing.

    Uses the single-shot bind-with-command pattern: the setPlaylist command
    is embedded in the initial bind POST body. The bind response sets the
    session cookie, and embedding the command in the same RPC avoids the
    two-request 410-Gone trap (where the SID returned from bind#1 isn't
    yet usable from bind#2 without proper cookie/sticky-routing handling).
    """
    state = load_state()
    if not state:
        raise YoutubeLoungeError(
            "not paired — call /youtube/pair with a TV code first"
        )
    params = {
        "device": "REMOTE_CONTROL",
        "id": state["device_id"],
        "name": state.get("name", "lgtv-core"),
        "app": "youtube-desktop",
        "loungeIdToken": state["lounge_token"],
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
        r = await client.post(YT_BIND, params=params, data=body)
        if r.status_code == 401:
            raise YoutubeLoungeError(
                "Lounge token rejected (expired?). Re-pair via /youtube/pair."
            )
        if r.status_code != 200:
            raise YoutubeLoungeError(
                f"setPlaylist failed: HTTP {r.status_code} — {r.text[:200]}"
            )


def status() -> dict[str, Any]:
    """Report whether we're paired and what the cached screen looks like.

    Synchronous because it only touches the local state file.
    """
    state = load_state()
    if not state:
        return {"paired": False}
    return {
        "paired": True,
        "screen_id": state.get("screen_id"),
        "device_id": state.get("device_id"),
        "expiration": state.get("expiration"),
        "name": state.get("name"),
    }
