"""Wake-on-LAN + post-wake TCP-readiness wait."""

from __future__ import annotations

import asyncio
import logging

from wakeonlan import send_magic_packet

log = logging.getLogger(__name__)


def send_wol(mac: str, broadcast_ip: str = "255.255.255.255") -> None:
    """Send a single magic packet. Idempotent — duplicates are harmless."""
    send_magic_packet(mac, ip_address=broadcast_ip, port=9)
    log.info("WoL sent to %s via %s:9", mac, broadcast_ip)


async def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    """Poll until a TCP connect succeeds or timeout elapses."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            w.close()
            await w.wait_closed()
            return True
        except (TimeoutError, OSError):
            await asyncio.sleep(0.5)
    return False
