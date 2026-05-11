"""Tiny operator CLI: `lgtv-core pair`, `lgtv-core serve`, `lgtv-core ping`."""

from __future__ import annotations

import asyncio

import typer
import uvicorn

from .config import load as load_settings
from .tv import TVDriver

app = typer.Typer(no_args_is_help=True, help="lgtv-core operator CLI")


@app.command()
def pair() -> None:
    """Run the one-time pairing handshake with the TV."""
    settings = load_settings()
    driver = TVDriver(settings)

    async def run() -> None:
        typer.echo(f"Pairing with {settings.host} ...")
        typer.echo("Watch the TV — accept the prompt on the physical remote.")
        key = await driver.pair()
        await driver.aclose()
        typer.echo(f"Paired. Client-key saved to {settings.state_path}")
        typer.echo(f"Key fingerprint: {key[:8]}...{key[-4:]}")

    asyncio.run(run())


@app.command()
def serve(
    bind: str = typer.Option(None, "--bind", help="Override LGTV_BIND"),
) -> None:
    """Run the HTTP daemon."""
    settings = load_settings()
    host_port = bind or settings.bind
    host, port_s = host_port.rsplit(":", 1)
    uvicorn.run(
        "lgtv_core.main:app",
        host=host,
        port=int(port_s),
        log_level=settings.log_level.lower(),
    )


@app.command()
def ping() -> None:
    """Wake the TV and fetch a state snapshot."""
    settings = load_settings()
    driver = TVDriver(settings)

    async def run() -> None:
        woke = await driver.wake()
        typer.echo(f"wake -> {woke}")
        snap = await driver.state_snapshot()
        await driver.aclose()
        typer.echo(snap)

    asyncio.run(run())


if __name__ == "__main__":
    app()
