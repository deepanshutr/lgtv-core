"""FastAPI app exposing the TVDriver as REST endpoints."""

from __future__ import annotations

import asyncio
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


class SoundOutputReq(BaseModel):
    output: str = Field(
        ...,
        description="One of: tv_speaker, external_arc, external_optical, bt_soundbar, headphone, tv_external_speaker",
    )


class PostKey(BaseModel):
    """A single key press scheduled after app launch."""

    name: str
    after_ms: int = Field(0, ge=0, le=30000, description="Wait this many ms before pressing")


class AppReq(BaseModel):
    id: str
    content_id: str | None = None
    params: dict[str, Any] | None = None
    post_launch_delay_ms: int = Field(
        0,
        ge=0,
        le=30000,
        description="Wait this many ms after the launch call returns before sending post_keys. Use for apps that need time to render a profile/welcome screen.",
    )
    post_keys: list[PostKey] | None = Field(
        None,
        description="Key presses to play AFTER the launch (e.g. ENTER to dismiss a profile screen). Each key's `after_ms` is the delay before pressing it (in addition to post_launch_delay_ms before the first one).",
    )


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

    async def _eager_connect() -> None:
        """Background WS warm-up so the first business call is hot. Must not
        block lifespan startup — uvicorn waits for lifespan-start to finish
        before binding the socket, so doing this synchronously would delay
        the daemon's readiness by ~3-4s."""
        try:
            await driver._ensure_client()
            log.info("Eagerly connected to TV at startup")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info(
                "Startup eager-connect skipped (TV likely off): %s",
                type(e).__name__,
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        warm_task = asyncio.create_task(_eager_connect())
        yield
        if not warm_task.done():
            warm_task.cancel()
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

    @app.get("/playback")
    async def get_playback() -> dict[str, Any]:
        return await _wrap(driver.get_playback)()

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

    @app.post("/sound_output")
    async def sound_output(req: SoundOutputReq) -> dict[str, Any]:
        return await _wrap(lambda: driver.set_sound_output(req.output))()

    @app.post("/app/launch")
    async def launch_app(req: AppReq) -> dict[str, Any]:
        post_keys = (
            [{"name": k.name, "after_ms": k.after_ms} for k in req.post_keys]
            if req.post_keys
            else None
        )
        return await _wrap(
            lambda: driver.launch_app(
                req.id,
                req.content_id,
                req.params,
                req.post_launch_delay_ms,
                post_keys,
            )
        )()

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
