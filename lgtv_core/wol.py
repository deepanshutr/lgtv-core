"""Wake-on-LAN + post-wake TCP-readiness wait."""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from wakeonlan import send_magic_packet

log = logging.getLogger(__name__)


def _subnet_broadcast(host_ip: str) -> str | None:
    """Derive /24 subnet broadcast from a unicast host IP.

    Assumes /24 — true for ~all home LANs. Returns None on bad input rather
    than guessing wider, so the caller can decide whether to fall back.
    """
    try:
        addr = ipaddress.IPv4Address(host_ip)
        return str(ipaddress.IPv4Network(f"{addr}/24", strict=False).broadcast_address)
    except (ipaddress.AddressValueError, ValueError):
        return None


def send_wol(mac: str, host: str | None = None) -> None:
    """Send magic packets to multiple targets for robustness.

    Routers commonly drop limited-broadcast (255.255.255.255) inside the LAN
    so a packet sent only there never reaches the TV NIC. Subnet-directed
    broadcast (e.g. 192.168.1.255) is forwarded reliably; unicast also works
    when the TV's NIC is in a low-power "listening" mode (per LG WebOS).
    Idempotent — duplicates are harmless.
    """
    targets: list[str] = []
    if host:
        subnet = _subnet_broadcast(host)
        if subnet:
            targets.append(subnet)
        targets.append(host)
    targets.append("255.255.255.255")
    for ip in targets:
        try:
            send_magic_packet(mac, ip_address=ip, port=9)
            log.info("WoL sent to %s via %s:9", mac, ip)
        except OSError as e:
            log.warning("WoL to %s:9 failed: %s", ip, e)


async def wait_for_port(host: str, port: int, timeout_s: float) -> bool:
    """Poll until a TCP connect succeeds or timeout elapses.

    Aggressive 100ms cadence with 300ms per-connect timeout. WoL → TCP-ready
    is typically 1.5-3s of TV-side NIC boot, so polling fast doesn't waste
    work but does cut latency by up to ~400ms vs. a 500ms cadence.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            _, w = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=0.3
            )
            w.close()
            await w.wait_closed()
            return True
        except (TimeoutError, OSError):
            await asyncio.sleep(0.1)
    return False
