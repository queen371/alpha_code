"""Shared SSRF-protection utilities.

Centralises ``is_private_ip``, ``is_private_ip_address``,
``validate_url`` and ``resolve_and_validate`` so every HTTP path in the
codebase uses the **same** fail-closed semantics.

Fail-closed means: if DNS resolution fails or the IP cannot be parsed,
the address is treated as private/blocked.  This is the secure default.
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def is_private_ip_address(ip_str: str) -> bool:
    """Return *True* if *ip_str* is a private / reserved / loopback address.

    **Fail-closed**: returns ``True`` on any parse error.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        return True


def is_private_ip(hostname: str) -> bool:
    """Return *True* if *hostname* resolves to any private / reserved IP.

    **Fail-closed**: returns ``True`` when DNS resolution fails.
    """
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for _family, _, _, _, sockaddr in addr_info:
            if is_private_ip_address(sockaddr[0]):
                return True
        return False
    except (socket.gaierror, ValueError, OSError):
        return True  # fail-closed


def validate_url(url: str) -> str | None:
    """Validate *url* for SSRF safety.

    Returns an error message (``str``) when the URL must be rejected,
    or ``None`` when the URL is safe to fetch.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"

    if parsed.scheme not in ("http", "https"):
        return f"Scheme '{parsed.scheme}' not allowed. Use http or https."

    hostname = parsed.hostname
    if not hostname:
        return "URL without hostname"

    if is_private_ip(hostname):
        return f"SSRF blocked: {hostname} resolves to private/reserved IP"

    return None


async def resolve_and_validate(hostname: str) -> str:
    """Resolve *hostname* once and validate the IP against SSRF rules.

    Returns the resolved IP string.  Raises ``ValueError`` when DNS
    fails or the IP is private (prevents DNS-rebinding attacks).
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
        if not infos:
            raise ValueError(f"DNS resolution failed for {hostname}")
        ip = infos[0][4][0]
    except socket.gaierror:
        raise ValueError(f"DNS resolution failed for {hostname}")

    if is_private_ip_address(ip):
        raise ValueError(f"IP {ip} is private (resolved from {hostname})")

    return ip
