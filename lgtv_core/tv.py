"""Thin async wrapper around aiowebostv.

One driver per process. Owns the WebSocket session, lazy-connects on first
use, reconnects on drop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiowebostv import WebOsClient, WebOsTvPairError

from . import state, wol
from .config import Settings

log = logging.getLogger(__name__)


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
    async def _ensure_client(self) -> WebOsClient:
        async with self._lock:
            if self._client is not None and self._client.is_connected():
                return self._client
            saved = state.load(self.settings.state_path)
            key = saved.get("client_key")
            self._client = WebOsClient(self.settings.host, client_key=key)
            await self._client.connect()
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

    async def wake(self) -> bool:
        wol.send_wol(self.settings.mac)
        return await wol.wait_for_port(self.settings.host, 3001, self.settings.wake_timeout)

    async def power_off(self) -> None:
        c = await self._ensure_client()
        await c.power_off()
        # WS will close on its end; drop our handle so next call reconnects.
        await self.aclose()

    async def state_snapshot(self) -> dict[str, Any]:
        c = await self._ensure_client()
        return {
            "current_app_id": c.current_app_id,
            "current_channel": c.current_channel,
            "muted": c.muted,
            "volume": c.volume,
            "apps": list(c.apps.keys()) if c.apps else [],
            "inputs": [i.id for i in (c.inputs or {}).values()] if c.inputs else [],
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

    async def launch_app(self, app_id: str) -> None:
        c = await self._ensure_client()
        await c.launch_app(app_id)

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
        await c.move_cursor(dx, dy)

    async def pointer_click(self) -> None:
        c = await self._ensure_client()
        await c.click_button()
