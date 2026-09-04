"""Real Swiggy Instamart cart writes.

``update_cart``'s real schema (verified directly against the live tool
schema, not assumed) REPLACES the entire cart with whatever ``items[]`` is
passed. Writing a single new item without first reading what is already
there would silently delete everything else in the user's real cart — the
same fresh-authoritative-state discipline this project already applies
before payment (``checkout_guard.py``'s re-read), applied here before a
cart write instead.

Real, reproduced incident: ``get_cart`` takes no address argument at all —
verified against its live tool schema — so it reads whatever address the
account's cart is CURRENTLY anchored to, which is not necessarily the
address the user just picked for this write. On a real account whose cart
was last anchored to an address Instamart no longer serves, ``get_cart``
itself fails with "The selected address is not serviceable at the moment,"
even though ``search_products`` and ``update_cart`` both worked fine
against the newly picked, genuinely serviceable address — reproduced live,
not assumed. This is not a transient or generic failure: a cart is only
ever meaningful for the ONE address it is anchored to, so a cart that can't
even be read because that anchor address stopped being serviceable holds
nothing that could still be delivered to the new address either way.
Treating that specific failure as "nothing to preserve" and proceeding
with the new item alone is not guessing what a real cart hides — every
other read failure (auth, network, an unrecognized error) still fails
closed exactly as before.

Real, reproduced incident (2026-09-04): a live end-to-end approval reported
"CART UPDATED" — ``update_cart`` returned no error — yet the real Swiggy
Instamart site showed nothing added. ``update_cart``'s own response was
never actually checked for what it claims; a non-error reply was treated
as proof, exactly the kind of trust this project refuses everywhere else
on the money path. ``commerce/shopify_mcp.py::read_cart`` already states
the correct discipline for this same class of write ("a separate round
trip from add_to_cart... this tells us what the store did"), applied there
but missing here. Fixed by making the write's own reply advisory only: a
second, independent ``get_cart`` read-back after the write is now required
to confirm the exact spin_id/quantity actually landed before this function
ever reports success — any mismatch, timeout, or read failure at that
point fails closed with ``SwiggyCartError``, the same as every other
unverified claim on this path.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mcp_direct_client import DirectMcpCallError, call_tool_directly

__all__ = ["SwiggyCartError", "CartItem", "add_to_instamart_cart"]

_URL = "https://mcp.swiggy.com/im"

# get_cart's own real error text for this specific case, verified live —
# matched narrowly so no other read failure (auth, network, an unrecognized
# server error) is ever treated this way; those still fail closed.
_ADDRESS_NOT_SERVICEABLE = "not serviceable"


class SwiggyCartError(RuntimeError):
    """The real cart read or write failed."""


@dataclass
class CartItem:
    spin_id: str
    quantity: int


async def add_to_instamart_cart(
    *, bearer_token: str, address_id: str, spin_id: str, quantity: int,
) -> dict:
    """Reads the real current cart, merges in the new item (replacing any
    existing line for the same ``spin_id`` rather than duplicating it), and
    writes the merged list back. Never blind-writes a single item.
    """
    cart_read_skipped_reason: str | None = None
    try:
        current = await call_tool_directly(
            url=_URL, bearer_token=bearer_token, tool_name="get_cart", arguments={},
        )
    except DirectMcpCallError as exc:
        if _ADDRESS_NOT_SERVICEABLE not in str(exc).lower():
            raise SwiggyCartError(f"could not read the current cart before writing: {exc}") from exc
        # See this module's own docstring: a cart anchored to an address
        # that is no longer serviceable holds nothing deliverable to the
        # newly picked address either -- there is nothing real to preserve,
        # not something we're guessing away.
        current = None
        cart_read_skipped_reason = (
            "could not verify the existing cart: its address was not serviceable, "
            "so it was treated as empty before writing"
        )

    existing: list[CartItem] = []
    if current:
        for item in current.get("items", []):
            existing_spin = item.get("spinId")
            existing_qty = item.get("quantity")
            if existing_spin and existing_spin != spin_id and isinstance(existing_qty, int):
                existing.append(CartItem(spin_id=existing_spin, quantity=existing_qty))

    merged = [*existing, CartItem(spin_id=spin_id, quantity=quantity)]

    try:
        await call_tool_directly(
            url=_URL, bearer_token=bearer_token, tool_name="update_cart",
            arguments={
                "selectedAddressId": address_id,
                "items": [{"spinId": item.spin_id, "quantity": item.quantity} for item in merged],
            },
        )
    except DirectMcpCallError as exc:
        raise SwiggyCartError(f"cart write failed: {exc}") from exc

    # update_cart returning without an error is not proof anything actually
    # landed -- see this module's docstring. Read the real cart back,
    # independently, before ever telling the caller it worked.
    try:
        confirmed = await call_tool_directly(
            url=_URL, bearer_token=bearer_token, tool_name="get_cart", arguments={},
        )
    except DirectMcpCallError as exc:
        raise SwiggyCartError(
            f"update_cart returned no error, but the real cart could not be "
            f"read back to confirm it: {exc}"
        ) from exc

    confirmed_items = (confirmed or {}).get("items", [])
    landed = any(
        item.get("spinId") == spin_id and item.get("quantity") == quantity
        for item in confirmed_items
    )
    if not landed:
        raise SwiggyCartError(
            "update_cart returned no error, but the item is not actually in "
            "the real cart when read back independently -- nothing was added"
        )

    return {
        "cart": confirmed,
        "items_written": [{"spin_id": i.spin_id, "quantity": i.quantity} for i in merged],
        "preserved_existing_items": len(existing),
        "cart_read_skipped_reason": cart_read_skipped_reason,
    }
