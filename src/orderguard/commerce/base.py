"""What every shop adapter must offer.

One interface, several shops behind it. Today: selected real Shopify stores
over MCP, plus our own FreshCart for offline tests. Tomorrow, anything that can
answer these four questions.

The important one is ``read_cart``. It reports what is **actually** in the cart,
read back from the shop — never what the agent believes it added. Everything
OrderGuard does rests on that distinction.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..models import CartLine, ObservedCart

__all__ = [
    "Offer", "CartLine", "ObservedCart", "CommerceAdapter",
    "AdapterError", "StoreUnavailable",
]

STRICT = ConfigDict(extra="forbid")


class AdapterError(RuntimeError):
    """A shop could not be reached or gave an answer we cannot use."""


class StoreUnavailable(AdapterError):
    """This particular shop is down or refused us. Others may still work."""


class Offer(BaseModel):
    """One buyable thing, from one shop.

    Money is **integer minor units** — paise for INR. Shopify happens to use the
    same convention, so no conversion is needed and no float ever appears.
    """
    model_config = STRICT

    store: str = Field(min_length=1)          # "slurrpfarm.com"
    store_label: str = ""                      # "Slurrp Farm"
    product_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    variant_title: str = ""
    price_minor: int = Field(ge=0)             # 26400 == ₹264.00
    currency: str = Field(min_length=3, max_length=3)
    available: bool = True
    url: str = ""
    image: str = ""

    def total_minor(self, quantity: int) -> int:
        return self.price_minor * quantity


@runtime_checkable
class CommerceAdapter(Protocol):
    """The four things a shop adapter must do for the guarded cart flow."""

    store: str
    store_label: str

    async def search(self, query: str, limit: int = 10) -> list[Offer]: ...

    async def add_to_cart(
        self, variant_id: str, quantity: int, cart_id: str | None = None
    ) -> ObservedCart: ...

    async def read_cart(self, cart_id: str) -> ObservedCart: ...
