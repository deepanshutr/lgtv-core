"""Smoke tests — no real TV required."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lgtv_core.main import create_app


@pytest.fixture
def client(fake_webos_client):  # fixture wires the stub
    _ = fake_webos_client
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_state_snapshot(client: TestClient, fake_webos_client) -> None:
    r = client.get("/state")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["reachable"] is True
    assert body["volume"] == 14
    assert "netflix" in body["apps"]


def test_state_snapshot_when_tv_unreachable(
    client: TestClient, fake_webos_client
) -> None:
    """If connect() raises TimeoutError, /state must still return 200 with
    a clean 'off' snapshot — not 502. This is the contract the Telegram
    /tv state command relies on."""
    fake_webos_client.is_connected = lambda: False
    fake_webos_client.connect.side_effect = TimeoutError()
    r = client.get("/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["reachable"] is False
    assert body["is_on"] is False
    assert body["volume"] is None
    assert body["apps"] == []


def test_volume_absolute(client: TestClient, fake_webos_client) -> None:
    r = client.post("/volume", json={"level": 30})
    assert r.status_code == 200
    fake_webos_client.set_volume.assert_awaited_once_with(30)


def test_volume_delta_up(client: TestClient, fake_webos_client) -> None:
    r = client.post("/volume", json={"delta": 3})
    assert r.status_code == 200
    assert fake_webos_client.volume_up.await_count == 3


def test_volume_xor_validation(client: TestClient) -> None:
    assert client.post("/volume", json={"level": 5, "delta": 1}).status_code == 400
    assert client.post("/volume", json={}).status_code == 400


def test_launch_app(client: TestClient, fake_webos_client) -> None:
    r = client.post("/app/launch", json={"id": "netflix"})
    assert r.status_code == 200
    fake_webos_client.launch_app.assert_awaited_once_with("netflix")


def test_key_press(client: TestClient, fake_webos_client) -> None:
    r = client.post("/key", json={"name": "home"})
    assert r.status_code == 200
    fake_webos_client.button.assert_awaited_once_with("HOME")


def test_key_unknown_returns_502(client: TestClient) -> None:
    # ValueError surfaces as 502 from the generic wrapper. We could
    # narrow this to 400 later if desired.
    r = client.post("/key", json={"name": "NOT_A_KEY"})
    assert r.status_code == 502


def test_pointer_move(client: TestClient, fake_webos_client) -> None:
    r = client.post("/pointer/move", json={"dx": 12, "dy": -4})
    assert r.status_code == 200
    fake_webos_client.move.assert_awaited_once_with(12, -4)
