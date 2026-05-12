"""Tests for SSDP discovery + DHCP-roll auto-heal path."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from lgtv_core import discover
from lgtv_core.main import create_app


# --------------------------------------------------------------------------- #
# Pure-logic unit tests for the matcher
# --------------------------------------------------------------------------- #
def test_matcher_recognises_webos() -> None:
    payload = (
        "HTTP/1.1 200 OK\r\nLocation: http://192.168.1.50:1359/\r\n"
        "Server: WebOS/4.1.0 UPnP/1.0\r\nUSN: uuid:abc::urn:...\r\n"
    )
    assert discover._matches(payload, uuid_match=None) is True


def test_matcher_recognises_lge() -> None:
    payload = "Server: Linux/i686 UPnP/1,0 LGE WebOS TV/Version 0.9\r\n"
    assert discover._matches(payload, uuid_match=None) is True


def test_matcher_ignores_other_devices() -> None:
    payload = "Server: Roku UPnP/1.0\r\n"
    assert discover._matches(payload, uuid_match=None) is False


def test_matcher_uuid_pin_matches() -> None:
    payload = (
        "USN: uuid:6c87792e-bad3-0889-3690-5ac9a8205c19::urn:lge-com:service:..."
    )
    assert (
        discover._matches(payload, uuid_match="6c87792e-bad3-0889-3690-5ac9a8205c19")
        is True
    )


def test_matcher_uuid_pin_rejects_different_tv() -> None:
    payload = "USN: uuid:deadbeef-0000-0000-0000-000000000000::urn:lge-com:..."
    assert (
        discover._matches(payload, uuid_match="6c87792e-bad3-0889-3690-5ac9a8205c19")
        is False
    )


# --------------------------------------------------------------------------- #
# Integration test: DHCP roll → SSDP rediscover → state.json persisted
# --------------------------------------------------------------------------- #
@pytest.fixture
def rolling_webos_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """First connect (to env IP) fails; second connect (to discovered IP) succeeds.

    Yields a dict with the captured host args so tests can assert on them.
    """
    seen_hosts: list[str] = []

    def make_client_for_host(*, host: str, fail: bool) -> MagicMock:
        c = MagicMock()
        c.is_connected = MagicMock(return_value=not fail)
        if fail:
            c.connect = AsyncMock(side_effect=OSError("ECONNREFUSED"))
        else:
            c.connect = AsyncMock()
        c.disconnect = AsyncMock()
        c.client_key = "fake-key"
        # state available only on a successful client
        s = MagicMock()
        s.is_on = True
        s.is_screen_on = True
        s.current_app_id = "com.webos.app.livetv"
        s.current_channel = None
        s.muted = False
        s.volume = 22
        s.sound_output = "external_arc"
        s.apps = {"netflix": MagicMock()}
        s.inputs = {}
        c.tv_state = s
        c.power_off = AsyncMock()
        return c

    failing = make_client_for_host(host="stale", fail=True)
    succeeding = make_client_for_host(host="discovered", fail=False)

    def factory(*args, **kwargs):
        host = args[0] if args else kwargs.get("host", "")
        seen_hosts.append(host)
        # First instantiation is for the env IP, second is for the rediscovered one.
        return failing if len(seen_hosts) == 1 else succeeding

    monkeypatch.setattr("lgtv_core.tv.WebOsClient", factory)
    # Stub SSDP to return a deterministic new IP.
    monkeypatch.setattr(
        "lgtv_core.tv.discover.discover_tv",
        AsyncMock(return_value="192.168.1.77"),
    )
    yield {"hosts": seen_hosts, "succeeding": succeeding}


def test_state_auto_heals_after_dhcp_roll(
    rolling_webos_client: dict, tmp_path: Path
) -> None:
    """When the env IP is stale and the TV is at a new IP, /state must
    rediscover via SSDP, connect to the new IP, and persist it so future
    calls skip the stale-IP detour."""
    # NB: the autouse _env fixture in conftest.py already set
    # LGTV_HOST=10.0.0.99 and a per-test LGTV_STATE_PATH under tmp_path.
    app = create_app()
    client = TestClient(app)

    r = client.get("/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["reachable"] is True
    assert body["volume"] == 22

    # Two WebOsClient(...) calls: first to env, then to rediscovered IP.
    hosts = rolling_webos_client["hosts"]
    assert hosts[0] == "10.0.0.99"
    assert hosts[1] == "192.168.1.77"

    # state.json must have last_known_host persisted so the next process
    # boot skips the SSDP detour entirely.
    state_path = Path(os.environ["LGTV_STATE_PATH"])
    assert state_path.exists()
    saved = json.loads(state_path.read_text())
    assert saved["last_known_host"] == "192.168.1.77"
