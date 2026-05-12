"""SSDP-based TV rediscovery for DHCP-roll recovery.

When the TV's IP changes (DHCP lease expiry, router reboot, network change),
the env-configured `LGTV_HOST` becomes stale and all connect attempts fail.
This module sends an SSDP M-SEARCH for LG webOS service descriptors and
returns the responder's current IP, so the daemon can auto-heal.

Only works when the TV is ON. In standby the NIC doesn't answer SSDP.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time

log = logging.getLogger(__name__)

SSDP_GROUP = "239.255.255.250"
SSDP_PORT = 1900
LG_ST = "urn:lge-com:service:webos-second-screen:1"


def _build_msearch(mx: int = 1) -> bytes:
    """Build an SSDP M-SEARCH request.

    `mx` is the max-wait hint to responders; we send a low value (1s) and
    burst multiple packets to keep median latency low while still allowing
    re-broadcasts in case of UDP loss.
    """
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_GROUP}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {LG_ST}\r\n"
        "\r\n"
    ).encode()


def _matches(payload: str, uuid_match: str | None) -> bool:
    """Decide whether an SSDP response identifies our TV."""
    low = payload.lower()
    if uuid_match:
        return f"uuid:{uuid_match.lower()}" in low
    return "webos" in low or "lge" in low


def _discover_blocking(timeout_s: float, uuid_match: str | None) -> str | None:
    """Send M-SEARCH, read responses until timeout or match.

    Sends an initial burst of 3 M-SEARCH packets (with MX=1) so a UDP drop
    on the first packet doesn't add 300ms-1s to discovery. The TV typically
    responds within 100ms of the first arriving packet.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        msg = _build_msearch(mx=1)
        for _ in range(3):
            sock.sendto(msg, (SSDP_GROUP, SSDP_PORT))
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(2048)
            except (TimeoutError, OSError):
                return None
            if _matches(data.decode(errors="ignore"), uuid_match):
                return str(addr[0])
    finally:
        sock.close()


async def discover_tv(
    timeout_s: float = 1.5, uuid_match: str | None = None
) -> str | None:
    """Find the LG TV's current LAN IP via SSDP, or None if not found.

    `uuid_match` (e.g. "6c87792e-bad3-0889-3690-5ac9a8205c19") pins discovery
    to a specific TV so neighbouring webOS sets can't be mistaken for ours.
    If absent, returns the first webOS responder.
    """
    return await asyncio.to_thread(_discover_blocking, timeout_s, uuid_match)
