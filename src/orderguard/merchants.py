"""Can we actually shop where the user asked?

Answered BEFORE searching, because the alternative is what the app used to do:
someone asks for pizza from La Pinoz, we ask their budget, search five grocery
shops, and offer them a mozzarella block from an organic farm store. Every step
technically worked and the result was nonsense.

A store the user names has one of four answers, and they are worth keeping
apart:

``SHOPPABLE``     we can search it and build a cart there
``BLOCKED``       real, and we are not allowed — Swiggy, Zomato
``NOT_REACHABLE`` no agent surface exists — Zepto, Blinkit, a local pizza place
``UNKNOWN``       we have not heard of it; worth probing before answering

Saying "I cannot shop there, here is why" is a better answer than a confident
list of the wrong products. Refusing early is the feature.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from .commerce.discovery import DiscoveryRefused, StoreCapability, discover
from .commerce.stores import ALL as VERIFIED_STORES
from .connectors import CONNECTORS, Status

__all__ = ["Reach", "MerchantVerdict", "resolve_merchant"]


class Reach(StrEnum):
    SHOPPABLE = "shoppable"
    BLOCKED = "blocked"
    NOT_REACHABLE = "not_reachable"
    UNKNOWN = "unknown"


class MerchantVerdict(NamedTuple):
    named: str
    reach: Reach
    domain: str = ""
    label: str = ""
    message: str = ""

    @property
    def can_shop(self) -> bool:
        return self.reach is Reach.SHOPPABLE


def _matches(name: str, *candidates: str) -> bool:
    cleaned = name.strip().lower().replace(" ", "")
    return any(cleaned == c.replace(" ", "").lower() for c in candidates if c)


async def resolve_merchant(
    named: str, extra_domains: tuple[tuple[str, str], ...] = ()
) -> MerchantVerdict:
    """Decide whether a named shop can be used, before anything is searched.

    ``extra_domains`` is ``((domain, label), ...)`` for stores this user added.
    """
    name = (named or "").strip()
    if not name:
        return MerchantVerdict("", Reach.UNKNOWN)

    # 0. our own demo shop. It is a real merchant in this repository — it is
    # where the Razorpay test payment runs, because a Shopify store collects its
    # own money and we will not pretend otherwise.
    if _matches(name, "freshcart", "freshcart_demo", "freshcart demo"):
        return MerchantVerdict(
            name, Reach.SHOPPABLE, domain="freshcart", label="FreshCart",
            message="Shopping at FreshCart, our own demo store.",
        )

    # 1. one of ours, or one this user added
    for domain, label in (
        [(s.domain, s.label) for s in VERIFIED_STORES] + list(extra_domains)
    ):
        if _matches(name, domain, label, domain.split(".")[0]):
            return MerchantVerdict(
                name, Reach.SHOPPABLE, domain=domain, label=label,
                message=f"Shopping at {label}.",
            )

    # 2. a platform we know about and cannot use. Say which, and why.
    for connector in CONNECTORS:
        if connector.kind == "payments":
            continue
        if not _matches(name, connector.id, connector.label):
            continue
        if connector.status is Status.LIVE:
            return MerchantVerdict(
                name, Reach.SHOPPABLE, domain=connector.id,
                label=connector.label, message=f"Shopping at {connector.label}.",
            )
        if connector.status in (Status.NEEDS_ACCESS, Status.RESTRICTED):
            return MerchantVerdict(
                name, Reach.BLOCKED, label=connector.label,
                message=(
                    f"I cannot shop {connector.label} for you. {connector.note} "
                    f"I can search stores I am allowed to use instead — "
                    f"say the word and I will."
                ),
            )
        return MerchantVerdict(
            name, Reach.NOT_REACHABLE, label=connector.label,
            message=(
                f"{connector.label} has no way for an assistant to shop it. "
                f"{connector.note}"
            ),
        )

    # 3. never heard of it. If it looks like a domain, ask the shop itself.
    if "." in name:
        try:
            found: StoreCapability = await discover(name)
        except DiscoveryRefused as exc:
            return MerchantVerdict(name, Reach.NOT_REACHABLE, message=str(exc))

        if found.shoppable:
            return MerchantVerdict(
                name, Reach.SHOPPABLE, domain=found.domain,
                label=found.domain.split(".")[0].title(),
                message=f"{found.domain} works — searching it now.",
            )
        return MerchantVerdict(name, Reach.NOT_REACHABLE, message=found.summary)

    # 4. a plain name we do not recognise: a restaurant, a local shop.
    return MerchantVerdict(
        name, Reach.NOT_REACHABLE,
        message=(
            f"I could not find a way to shop {name}. If it has a website, give "
            f"me the address and I will check it. Otherwise I can search the "
            f"stores I can reach, or look on the web so you can open it yourself."
        ),
    )
