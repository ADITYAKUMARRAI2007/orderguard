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

Real, reproduced incident (2026-09-04, same session): the pre-write
``get_cart`` read then failed on every single approval, across many
distinct real missions and items, for over an hour straight, always with
"The store is unavailable at the moment. Please try again after some
time." -- while ``search_products``, run moments earlier in the SAME
turn against the SAME address, kept returning full real catalogs the
entire time. A genuinely closed or degraded store would fail search too;
it never did. This is the same root shape as the "not serviceable" case
above, just Swiggy's OTHER wording for it: ``get_cart`` has no address
argument, so it is reading whatever address the account's cart is stuck
on from a PRIOR session -- not the one just searched -- and that stale
anchor's store being paused, closed, or unavailable says nothing about
whether the store just searched can be written to. Recognized as the same
"nothing real to preserve" case, under the same reasoning: a cart that
cannot even be read because its own anchor point is unavailable holds
nothing that could be merged into a write going to a different address
anyway.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .mcp_direct_client import DirectMcpCallError, call_tool_directly

__all__ = ["SwiggyCartError", "CartItem", "add_to_instamart_cart"]

_URL = "https://mcp.swiggy.com/im"

# get_cart's own real error text for this class of failure -- a stale cart
# anchor, not a real block on the write -- verified live in two distinct
# wordings. Matched narrowly so no other read failure (auth, network, an
# unrecognized server error) is ever treated this way; those still fail
# closed.
_STALE_CART_ANCHOR_MARKERS = ("not serviceable", "store is unavailable")


class SwiggyCartError(RuntimeError):
    """The real cart read or write failed."""


@dataclass
class CartItem:
    spin_id: str
    quantity: int


def _cart_shape(cart: dict | None) -> object:
    """Item ids and quantities only — never the whole payload. Swiggy's cart
    carries names, images and promo prose; the only thing worth an operator's
    attention here is which ids/quantities were really there."""
    if cart is None:
        return None
    items = cart.get("items")
    if not isinstance(items, list):
        return f"<no items list; keys={sorted(cart)}>"
    return [(item.get("spinId"), item.get("quantity")) for item in items]


def _log_write_evidence(
    before: dict | None, sent: dict, reply: dict | None, after: dict | None,
) -> None:
    """Operator-only, stderr, on failure paths only — same F-017 discipline
    as the normalizer's diagnostics: the user-facing error stays generic,
    while the real shapes needed to diagnose it are not thrown away.

    ``reply`` in particular was previously discarded entirely. F-036 proved
    it carries Swiggy's own explanation of what it did to the cart ("no valid
    items remained, so the cart is now empty") — evidence that was sitting
    on the wire, unread, while the failure it explains stayed a mystery.
    """
    print(
        "[swiggy_cart] cart write did not verify:\n"
        f"  before  = {_cart_shape(before)}\n"
        f"  sent    = {sent.get('items')} to address {sent.get('selectedAddressId')!r}\n"
        f"  reply   = {reply!r}\n"
        f"  after   = {_cart_shape(after)}",
        file=sys.stderr, flush=True,
    )


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
        exc_text = str(exc).lower()
        matched_marker = next(
            (marker for marker in _STALE_CART_ANCHOR_MARKERS if marker in exc_text), None,
        )
        if matched_marker is None:
            raise SwiggyCartError(f"could not read the current cart before writing: {exc}") from exc
        # See this module's own docstring: a cart whose own anchor point
        # can't even be read (address no longer serviceable, or that
        # anchor's store currently unavailable) holds nothing deliverable
        # through THIS write either -- there is nothing real to preserve,
        # not something we're guessing away.
        current = None
        cart_read_skipped_reason = (
            f"could not verify the existing cart ({matched_marker}), "
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

    write_arguments = {
        "selectedAddressId": address_id,
        "items": [{"spinId": item.spin_id, "quantity": item.quantity} for item in merged],
    }
    try:
        write_reply = await call_tool_directly(
            url=_URL, bearer_token=bearer_token, tool_name="update_cart",
            arguments=write_arguments,
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
        _log_write_evidence(current, write_arguments, write_reply, None)
        raise SwiggyCartError(
            f"update_cart returned no error, but the real cart could not be "
            f"read back to confirm it: {exc}"
        ) from exc

    confirmed_items = (confirmed or {}).get("items", [])
    landed = any(
        item.get("spinId") == spin_id and item.get("quantity") == quantity
        for item in confirmed_items
    )

    # A write that fails does not politely leave the rest of the cart alone.
    # Swiggy's own reply to a write containing one unsellable item, captured
    # live in F-036, is "no valid items remained, so the cart is now empty" —
    # it drops the invalid item AND everything that was already there. Items
    # this write was supposed to be PRESERVING can therefore be destroyed by
    # it, silently, which is exactly what a user sees as "the things I added
    # earlier disappeared." Checked explicitly, and reported, rather than
    # left for the user to discover on the merchant's own site.
    confirmed_spins = {item.get("spinId") for item in confirmed_items}
    lost = [item.spin_id for item in existing if item.spin_id not in confirmed_spins]

    if not landed or lost:
        _log_write_evidence(current, write_arguments, write_reply, confirmed)
    if not landed:
        detail = (
            f" It also removed {len(lost)} item(s) that were already in your "
            "cart before this attempt — Swiggy empties the whole cart when it "
            "rejects a write, so re-add them."
            if lost else ""
        )
        raise SwiggyCartError(
            "update_cart returned no error, but the item is not actually in "
            "the real cart when read back independently -- nothing was added."
            + detail
        )
    if lost:
        raise SwiggyCartError(
            f"the item was added, but the write removed {len(lost)} item(s) "
            "that were already in your cart — Swiggy rebuilt the cart around "
            "this write instead of merging into it, so the cart no longer "
            "matches what you approved"
        )

    return {
        "cart": confirmed,
        "items_written": [{"spin_id": i.spin_id, "quantity": i.quantity} for i in merged],
        "preserved_existing_items": len(existing),
        "cart_read_skipped_reason": cart_read_skipped_reason,
    }
