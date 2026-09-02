"""Custom-connector URL validation. A "paste a remote MCP URL" feature is a
textbook SSRF vector, so every call site (registration AND every reuse — a
DNS answer can change between the two, which is exactly the rebinding gap
this module closes by re-resolving on every call, not just at registration)
must go through ``assert_safe_url`` immediately before connecting.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

__all__ = ["SSRFRejected", "assert_safe_url"]

_LOCALHOST_DEV_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SSRFRejected(RuntimeError):
    pass


def _is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def assert_safe_url(url: str, *, allow_localhost_dev: bool = False) -> None:
    """Raises ``SSRFRejected`` unless ``url`` is HTTPS and resolves to a
    public IP address. ``allow_localhost_dev`` exists only for this
    project's own dev-mode Swiggy callback and defaults off — a
    user-pasted custom-connector URL must never be granted this exception.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if allow_localhost_dev and host in _LOCALHOST_DEV_HOSTS:
        return

    if parsed.scheme != "https":
        raise SSRFRejected(f"custom connector URL must be HTTPS: {url!r}")
    if not host:
        raise SSRFRejected(f"custom connector URL has no host: {url!r}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFRejected(f"could not resolve host {host!r}: {exc}") from exc

    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if _is_private(ip):
            raise SSRFRejected(f"host {host!r} resolves to a private/internal address: {addr}")


def assert_no_cross_host_redirect(original_url: str, final_url: str) -> None:
    if urlparse(original_url).hostname != urlparse(final_url).hostname:
        raise SSRFRejected(
            f"refused a cross-host redirect from {original_url!r} to {final_url!r}"
        )
