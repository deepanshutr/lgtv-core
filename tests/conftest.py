"""Shared fixtures. Stubs out aiowebostv so unit tests run without a TV."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LGTV_HOST", "10.0.0.99")
    monkeypatch.setenv("LGTV_MAC", "aa:bb:cc:dd:ee:ff")
    monkeypatch.setenv("LGTV_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("LGTV_BIND", "127.0.0.1:0")
    # ensure we don't accidentally read a real ~/.config/lgtv/state.env
    monkeypatch.setenv("LGTV_LOG_LEVEL", "WARNING")
    os.environ.pop("LGTV_WAKE_TIMEOUT", None)


@pytest.fixture
def fake_webos_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    client.is_connected = MagicMock(return_value=True)
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.client_key = "fake-key-abcdef0123"
    client.current_app_id = "com.webos.app.livetv"
    client.current_channel = None
    client.muted = False
    client.volume = 14
    client.apps = {"netflix": MagicMock(), "youtube.leanback.v4": MagicMock()}
    client.inputs = {}
    client.power_off = AsyncMock()
    client.set_volume = AsyncMock()
    client.volume_up = AsyncMock()
    client.volume_down = AsyncMock()
    client.set_mute = AsyncMock()
    client.launch_app = AsyncMock()
    client.set_input = AsyncMock()
    client.button = AsyncMock()
    client.move_cursor = AsyncMock()
    client.click_button = AsyncMock()

    def _factory(*args, **kwargs):
        # capture the key the driver tried to use
        client._init_key = kwargs.get("client_key")
        return client

    monkeypatch.setattr("lgtv_core.tv.WebOsClient", _factory)
    return client
