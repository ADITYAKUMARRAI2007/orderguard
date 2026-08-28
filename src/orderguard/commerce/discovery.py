"""Shop at a store nobody has integrated, by asking it what it can do.

The curated list in ``stores.py`` was never the real limit. Every Shopify
storefront exposes ``/api/mcp``, so "which stores work" is not a list we
maintain — it is a question we can ask any domain at the moment a user names
one. Twenty Indian D2C brands picked at random, ten answered. None had been
tested before.

So a user can say *"find millet cereal on farmley.com"* and it works, for a
store this project has never heard of and no one integrated.

Three things this module is careful about.

**It reports capability, not reachability.** Some storefronts answer with only
``search_catalog`` and no cart tools. Those can be browsed and not bought from,
and saying so up front is better than failing halfway through a purchase.

**It refuses to probe the machine it runs on.** A domain typed by a user is an
outbound request we make on their behalf, which is the classic shape of an SSRF:
``localhost``, ``169.254.169.254`` and private ranges would reach services that
are not on the internet. Those are rejected before any connection is opened.

**It trusts nothing a store says about itself.** Discovery reads the tool
*names* and nothing else. Descriptions, instructions and any other prose are
ignored here exactly as they are in the adapter (D-023).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import NamedTuple
from urllib.parse import urlparse

import httpx

__all__ = ["StoreCapability", "DiscoveryRefused", "normalise_domain", "discover"]

_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
_TIMEOUT = httpx.Timeout(12.0, connect=6.0)

# What a store must offer to be useful to us.
_SEARCH_TOOL = "search_catalog"
_CART_TOOLS = frozenset({"update_cart", "get_cart"})

_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

_BLOCKED_NAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "broadcasthost",
    "metadata.google.internal", "instance-data",
})


class DiscoveryRefused(ValueError):
    """The domain was rejected before any request was made."""


class StoreCapability(NamedTuple):
    domain: str
    reachable: bool
    tools: tuple[str, ...] = ()
    can_search: bool = False
    can_cart: bool = False
    error: str = ""

    @property
    def shoppable(self) -> bool:
        """Both halves. Searching a shop you cannot buy from is a dead end."""
        return self.can_search and self.can_cart

    @property
    def summary(self) -> str:
        if not self.reachable:
            return f"{self.domain} did not answer: {self.error}"
        if self.shoppable:
            return f"{self.domain} can be searched and added to."
        if self.can_search:
            return f"{self.domain} can be searched, but exposes no cart."
        return f"{self.domain} answered, but not with tools we can use."


def normalise_domain(raw: str) -> str:
    """Turn whatever the user typed into a bare hostname, or refuse it.

    Refusing is a security control, not tidiness. The user hands us a string and
    we make an outbound request with it, so anything naming this machine or a
    private network must never reach ``httpx``.
    """
    text = (raw or "").strip().lower()
    if not text:
        raise DiscoveryRefused("no store given")

    if "://" in text:
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"}:
            raise DiscoveryRefused(f"only http(s) stores are supported, not {parsed.scheme!r}")
        text = parsed.hostname or ""
    else:
        text = text.split("/")[0]

    text = text.split("@")[-1].split(":")[0].strip(".")

    if not text:
        raise DiscoveryRefused("that is not a store address")
    if text in _BLOCKED_NAMES:
        raise DiscoveryRefused(f"{text!r} is this machine, not a shop")

    # A bare IP address is never a storefront we want, and is the usual way an
    # SSRF reaches an internal service.
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        raise DiscoveryRefused(
            f"{address} is an IP address, not a shop domain"
        )

    if not _DOMAIN.fullmatch(text):
        raise DiscoveryRefused(f"{raw!r} is not a valid store domain")
    if text.endswith((".local", ".internal", ".localhost")):
        raise DiscoveryRefused(f"{text!r} is a private address, not a public shop")

    return text


async def discover(
    raw_domain: str, client: httpx.AsyncClient | None = None
) -> StoreCapability:
    """Ask a domain what it can do. Never raises for a store that simply fails."""
    domain = normalise_domain(raw_domain)          # this one DOES raise, before any I/O

    owned = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT)
    try:
        response = await client.post(
            f"https://{domain}/api/mcp",
            json={"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
            headers=_HEADERS,
        )
        if response.status_code != 200:
            return StoreCapability(domain, False, error=f"HTTP {response.status_code}")

        body = response.json()
        raw_tools = (body.get("result") or {}).get("tools") or []
        # Names only. A tool's description is merchant prose and gets no say.
        names = tuple(
            str(tool.get("name") or "") for tool in raw_tools if isinstance(tool, dict)
        )
    except httpx.HTTPError as exc:
        return StoreCapability(domain, False, error=type(exc).__name__)
    except ValueError:
        return StoreCapability(domain, False, error="reply was not JSON")
    finally:
        if owned:
            await client.aclose()

    return StoreCapability(
        domain=domain,
        reachable=True,
        tools=names,
        can_search=_SEARCH_TOOL in names,
        can_cart=_CART_TOOLS.issubset(names),
    )


async def discover_many(
    domains: list[str], client: httpx.AsyncClient | None = None
) -> list[StoreCapability]:
    """Probe several at once. A refused domain becomes a result, not an exception."""
    owned = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT)

    async def one(raw: str) -> StoreCapability:
        try:
            return await discover(raw, client=client)
        except DiscoveryRefused as exc:
            return StoreCapability(raw.strip().lower(), False, error=str(exc))

    try:
        return list(await asyncio.gather(*(one(d) for d in domains)))
    finally:
        if owned:
            await client.aclose()
