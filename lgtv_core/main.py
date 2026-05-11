"""FastAPI app exposing the TVDriver as REST endpoints."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import load as load_settings
from .tv import TVDriver

log = logging.getLogger("lgtv_core")


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class VolumeReq(BaseModel):
    level: int | None = Field(None, ge=0, le=100)
    delta: int | None = None


class MuteReq(BaseModel):
    on: bool


class AppReq(BaseModel):
    id: str


class InputReq(BaseModel):
    id: str


class KeyReq(BaseModel):
    name: str


class PointerMoveReq(BaseModel):
    dx: int
    dy: int


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    driver = TVDriver(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await driver.aclose()

    app = FastAPI(title="lgtv-core", version="0.1.0", lifespan=lifespan)

    def _wrap(coro):
        async def runner() -> dict[str, Any]:
            try:
                result = await coro()
                if isinstance(result, dict):
                    return {"ok": True, **result}
                return {"ok": True, "result": result}
            except Exception as e:
                log.exception("driver call failed")
                raise HTTPException(status_code=502, detail=str(e)) from e
        return runner

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/wake")
    async def wake() -> dict[str, Any]:
        return await _wrap(lambda: driver.wake())()

    @app.post("/power/off")
    async def power_off() -> dict[str, Any]:
        return await _wrap(lambda: driver.power_off())()

    @app.get("/state")
    async def get_state() -> dict[str, Any]:
        return await _wrap(driver.state_snapshot)()

    @app.post("/volume")
    async def volume(req: VolumeReq) -> dict[str, Any]:
        if (req.level is None) == (req.delta is None):
            raise HTTPException(400, "specify exactly one of {level, delta}")
        if req.level is not None:
            return await _wrap(lambda: driver.set_volume(req.level))()  # type: ignore[arg-type]
        return await _wrap(lambda: driver.volume_delta(req.delta))()  # type: ignore[arg-type]

    @app.post("/mute")
    async def mute(req: MuteReq) -> dict[str, Any]:
        return await _wrap(lambda: driver.set_mute(req.on))()

    @app.post("/app/launch")
    async def launch_app(req: AppReq) -> dict[str, Any]:
        return await _wrap(lambda: driver.launch_app(req.id))()

    @app.post("/input/switch")
    async def switch_input(req: InputReq) -> dict[str, Any]:
        return await _wrap(lambda: driver.switch_input(req.id))()

    @app.post("/key")
    async def press_key(req: KeyReq) -> dict[str, Any]:
        return await _wrap(lambda: driver.press_key(req.name))()

    @app.post("/pointer/move")
    async def pointer_move(req: PointerMoveReq) -> dict[str, Any]:
        return await _wrap(lambda: driver.pointer_move(req.dx, req.dy))()

    @app.post("/pointer/click")
    async def pointer_click() -> dict[str, Any]:
        return await _wrap(driver.pointer_click)()

    return app


def __getattr__(name: str):
    """Lazily create the ASGI app so importing this module doesn't require env.

    Importing `lgtv_core.main` for tests should NOT trigger a real settings
    load. uvicorn's "lgtv_core.main:app" import path goes through this hook
    on attribute access, so the daemon path still gets a real app.
    """
    if name == "app":
        global_app = create_app()
        globals()["app"] = global_app
        return global_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
