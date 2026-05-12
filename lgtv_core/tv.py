"""Thin async wrapper around aiowebostv.

One driver per process. Owns the WebSocket session, lazy-connects on first
use, reconnects on drop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiowebostv import WebOsClient, WebOsTvPairError

from . import discover, state, wol
from .config import Settings

log = logging.getLogger(__name__)


def _unreachable_snapshot() -> dict[str, Any]:
    """Canonical 'TV is off / unreachable' state. Always shape-compatible
    with a real state so clients can render either uniformly."""
    return {
        "reachable": False,
        "is_on": False,
        "is_screen_on": False,
        "current_app_id": None,
        "current_channel": None,
        "muted": None,
        "volume": None,
        "sound_output": None,
        "apps": [],
        "inputs": [],
    }


KEY_TO_BUTTON = {
    "HOME": "HOME",
    "BACK": "BACK",
    "MENU": "MENU",
    "UP": "UP",
    "DOWN": "DOWN",
    "LEFT": "LEFT",
    "RIGHT": "RIGHT",
    "ENTER": "ENTER",
    "EXIT": "EXIT",
    "PLAY": "PLAY",
    "PAUSE": "PAUSE",
    "STOP": "STOP",
    "RED": "RED",
    "GREEN": "GREEN",
    "YELLOW": "YELLOW",
    "BLUE": "BLUE",
}


class TVDriver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: WebOsClient | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _active_host(self, saved: dict[str, Any] | None = None) -> str:
        """The IP to use right now: last-discovered (state.json) wins over env."""
        if saved is None:
            saved = state.load(self.settings.state_path)
        return saved.get("last_known_host") or self.settings.host

    async def _rediscover(self, saved: dict[str, Any], previous_host: str) -> str | None:
        """SSDP-probe for the TV and persist the new IP if it changed.

        Only succeeds when the TV is ON (NIC answers SSDP). Returns the
        discovered IP, or None if no LG webOS device responded.
        """
        discovered = await discover.discover_tv(
            timeout_s=3.0, uuid_match=saved.get("uuid")
        )
        if discovered and discovered != previous_host:
            saved["last_known_host"] = discovered
            state.save(self.settings.state_path, saved)
            log.warning(
                "SSDP rediscovered TV at %s (was %s) — persisted to state.json",
                discovered,
                previous_host,
            )
        return discovered

    _CONNECT_TIMEOUT_S = 2.5

    async def _connect_with_timeout(self, client: WebOsClient) -> None:
        """Bound the WS connect so a bogus IP fails fast instead of hanging
        on the OS-default TCP timeout (often 60-130s)."""
        await asyncio.wait_for(client.connect(), timeout=self._CONNECT_TIMEOUT_S)

    async def _ensure_client(self) -> WebOsClient:
        async with self._lock:
            if self._client is not None and self._client.is_connected():
                return self._client
            saved = state.load(self.settings.state_path)
            key = saved.get("client_key")
            host = self._active_host(saved)
            try:
                self._client = WebOsClient(host, client_key=key)
                await self._connect_with_timeout(self._client)
            except (OSError, ConnectionError, TimeoutError):
                # Maybe the TV's IP rolled. SSDP-rediscover and retry once.
                self._client = None
                discovered = await self._rediscover(saved, host)
                if not discovered or discovered == host:
                    raise
                self._client = WebOsClient(discovered, client_key=key)
                await self._connect_with_timeout(self._client)
            # Persist any newly-issued key
            if self._client.client_key and self._client.client_key != key:
                saved["client_key"] = self._client.client_key
                state.save(self.settings.state_path, saved)
            return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def pair(self) -> str:
        """Run pairing handshake. User must accept on-screen prompt."""
        saved = state.load(self.settings.state_path)
        client = WebOsClient(self.settings.host, client_key=saved.get("client_key"))
        try:
            await client.connect()
        except WebOsTvPairError as e:
            raise RuntimeError(
                "TV rejected pairing. Did you accept the on-screen prompt?"
            ) from e
        assert client.client_key is not None
        saved["client_key"] = client.client_key
        state.save(self.settings.state_path, saved)
        await client.disconnect()
        return client.client_key

    async def wake(self) -> None:
        saved = state.load(self.settings.state_path)
        host = self._active_host(saved)
        wol.send_wol(self.settings.mac, host=host)
        ready = await wol.wait_for_port(host, 3001, self.settings.wake_timeout)
        if not ready:
            # Stale IP? If the TV is on under a new lease, SSDP will find it.
            discovered = await self._rediscover(saved, host)
            if discovered and discovered != host:
                wol.send_wol(self.settings.mac, host=discovered)
                ready = await wol.wait_for_port(
                    discovered, 3001, max(5, self.settings.wake_timeout // 2)
                )
                host = discovered
        if not ready:
            raise RuntimeError(
                f"TV at {host} did not become reachable on :3001 "
                f"within {self.settings.wake_timeout}s. "
                "If the TV is fully powered off, WoL cannot wake it — check "
                "Settings → General → Quick Start+ and Settings → Connection → "
                "Mobile TV On."
            )
        # Pre-warm the WS connection so the next /state / /volume / /key call
        # doesn't pay the TLS-handshake cost on a freshly-woken TV. Failure
        # here is non-fatal (the next user call will retry).
        try:
            await self._ensure_client()
        except Exception as e:
            log.info("wake: pre-warm connect failed (non-fatal): %s", type(e).__name__)

    async def power_off(self) -> None:
        c = await self._ensure_client()
        await c.power_off()
        # WS will close on its end; drop our handle so next call reconnects.
        await self.aclose()

    async def state_snapshot(self) -> dict[str, Any]:
        """Return the current TV state.

        If the TV is unreachable (in deep standby, off, or LAN-disconnected),
        we surface that as a clean `reachable=False, is_on=False` snapshot
        rather than raising — clients use this endpoint to *check* state,
        and "TV is off" is a valid answer, not an error.
        """
        try:
            c = await self._ensure_client()
        except (TimeoutError, OSError, ConnectionError) as e:
            log.info("state_snapshot: TV unreachable (%s)", type(e).__name__)
            # Drop any stale client so the next call starts clean.
            self._client = None
            return _unreachable_snapshot()
        s = c.tv_state
        return {
            "reachable": True,
            "is_on": s.is_on,
            "is_screen_on": s.is_screen_on,
            "current_app_id": s.current_app_id,
            "current_channel": s.current_channel,
            "muted": s.muted,
            "volume": s.volume,
            "sound_output": s.sound_output,
            "apps": list(s.apps.keys()) if s.apps else [],
            "inputs": list(s.inputs.keys()) if s.inputs else [],
        }

    async def set_volume(self, level: int) -> None:
        c = await self._ensure_client()
        await c.set_volume(level)

    async def volume_delta(self, delta: int) -> None:
        c = await self._ensure_client()
        for _ in range(abs(delta)):
            if delta > 0:
                await c.volume_up()
            else:
                await c.volume_down()

    async def set_mute(self, on: bool) -> None:
        c = await self._ensure_client()
        await c.set_mute(on)

    async def set_sound_output(self, output: str) -> None:
        """Route TV audio to a specific output. Common values:
        tv_speaker, external_arc, external_optical, bt_soundbar, headphone."""
        c = await self._ensure_client()
        await c.change_sound_output(output)

    async def get_playback(self) -> dict[str, Any]:
        """Return media-playback info that's NOT in state_snapshot.

        Useful for distinguishing "YouTube launched but parked on a profile
        picker" from "YouTube actually playing a video". The TV doesn't
        expose what specific YouTube video is playing (that's app-internal
        state).

        Each probe is independent: older WebOS firmwares (CX, 2020) don't
        ship `com.webos.media/getForegroundAppInfo` and return a 404 for
        it, so we surface that error per-field rather than failing the
        whole endpoint.
        """
        c = await self._ensure_client()
        out: dict[str, Any] = {}
        for key, fn in (
            ("media_foreground", c.get_media_foreground_app),
            ("audio_status", c.get_audio_status),
            ("current_app_id", c.get_current_app),
        ):
            try:
                out[key] = await fn()
            except Exception as e:
                out[key] = {"error": str(e), "type": type(e).__name__}
        return out

    async def launch_app(
        self,
        app_id: str,
        content_id: str | None = None,
        params: dict[str, Any] | None = None,
        post_launch_delay_ms: int = 0,
        post_keys: list[dict[str, Any]] | None = None,
    ) -> None:
        """Launch an app, optionally deep-linking and then driving the remote.

        - `content_id`: app-specific deep-link key (e.g. YouTube video ID).
        - `params`: arbitrary launch params (e.g. {"contentTarget": "..."}).
          Mutually exclusive with content_id.
        - `post_launch_delay_ms`: wait this long before the first post_key.
          Use for apps that need to render a profile/welcome screen first.
        - `post_keys`: list of {name, after_ms} dicts. Each key's after_ms is
          the delay BEFORE pressing it (cumulative on top of post_launch_delay_ms
          for the first one). Lets us dismiss profile pickers, hit Play, etc.
          all in one daemon call instead of N curl roundtrips.
        """
        c = await self._ensure_client()
        if content_id is not None:
            await c.launch_app_with_content_id(app_id, content_id)
        elif params is not None:
            await c.launch_app_with_params(app_id, params)
        else:
            await c.launch_app(app_id)

        if post_launch_delay_ms > 0:
            await asyncio.sleep(post_launch_delay_ms / 1000.0)
        if post_keys:
            for k in post_keys:
                after_ms = int(k.get("after_ms", 0) or 0)
                if after_ms > 0:
                    await asyncio.sleep(after_ms / 1000.0)
                button = KEY_TO_BUTTON.get(str(k["name"]).upper())
                if button is None:
                    log.warning("launch_app: unknown post_key %r, skipping", k["name"])
                    continue
                await c.button(button)

    async def switch_input(self, input_id: str) -> None:
        c = await self._ensure_client()
        await c.set_input(input_id)

    async def press_key(self, name: str) -> None:
        button = KEY_TO_BUTTON.get(name.upper())
        if button is None:
            raise ValueError(f"unknown key {name!r}; see lgtv_core.tv.KEY_TO_BUTTON")
        c = await self._ensure_client()
        await c.button(button)

    async def pointer_move(self, dx: int, dy: int) -> None:
        c = await self._ensure_client()
        await c.move(dx, dy)

    async def pointer_click(self) -> None:
        c = await self._ensure_client()
        await c.click()
