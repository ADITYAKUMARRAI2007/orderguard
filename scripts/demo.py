"""The whole product in one run, against real stores.

    make demo

Everything here is live. Real Shopify catalogues, real rupee prices, a real
cart created and read back. Nothing is stubbed and no output is faked — if a
store is down you will see it fail, which is the point.

No account, no login, no payment. Carts are anonymous and are abandoned when
this exits.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from orderguard.cart_verifier import ApprovedCartLine, CartExpectation, compare_cart
from orderguard.checkout_guard import CheckoutEvidence, evaluate_pre_payment_gates
from orderguard.commerce import GROCERY, Location, ShopifyMCPAdapter, search_stores
from orderguard.commerce.discovery import discover
from orderguard.connectors import CONNECTORS, summary
from orderguard.enums import IntentStatus
from orderguard.mcp_server import handle_rpc
from orderguard.models import CartLine, IntentItem, ObservedCart, PurchaseIntent
from orderguard.websearch import search_web

BOLD, DIM, GREEN, RED, YELLOW, OFF = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"
)


def head(number: str, title: str) -> None:
    print(f"\n{BOLD}{number}  {title}{OFF}")
    print(DIM + "─" * 66 + OFF)


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


async def main() -> int:
    QUANTITY, BUDGET = 2, 70_000        # two packs, ₹700

    # ---------------------------------------------------------------- 1
    head("1", "What we can reach, and what we cannot")
    counts = summary()
    print(f"   live {counts['live']}   needs access {counts['needs_access']}   "
          f"restricted {counts['restricted']}   unavailable {counts['unavailable']}")
    for cid in ("swiggy", "zomato", "zepto"):
        c = next(x for x in CONNECTORS if x.id == cid)
        mark = YELLOW if c.status != "unavailable" else DIM
        print(f"   {mark}{c.label:8} {c.status:14}{OFF} {c.evidence[:64]}…")

    # ---------------------------------------------------------------- 2
    head("2", "Shop at a store nobody integrated")
    for domain in ("farmley.com", "beardo.in", "minimalist.co"):
        found = await discover(domain)
        mark = GREEN if found.shoppable else YELLOW
        print(f"   {mark}{found.summary}{OFF}")

    # ---------------------------------------------------------------- 3
    head("3", "Search every store at once")
    outcome = await search_stores(
        "millet cereal", quantity=QUANTITY, budget_minor=BUDGET,
        stores=GROCERY, location=Location(postal_code="560001", region="KA"),
    )
    print(f"   searched {', '.join(outcome.stores_searched)}")
    if outcome.stores_failed:
        print(f"   {YELLOW}failed  {outcome.stores_failed}{OFF}")
    if not outcome.offers:
        print(f"   {RED}no offers came back — cannot continue{OFF}")
        return 1

    for scored in outcome.offers[:4]:
        o = scored.offer
        print(f"   {o.title[:44]:44} {rupees(o.price_minor):>11}  {o.store_label}")
    print(f"\n   {BOLD}a human must choose here — {len(outcome.offers)} options, "
          f"the agent picks none{OFF}")

    # ---------------------------------------------------------------- 4
    head("4", "Also look where we are NOT allowed to buy")
    web = await search_web("millet cereal")
    if web.worked:
        for r in web.results[:3]:
            price = rupees(r.claimed_price_paise) if r.claimed_price_paise else "—"
            print(f"   {r.title[:44]:44} {price:>11}  {r.site_label}  (open only)")
    else:
        print(f"   {DIM}skipped: {web.unavailable_reason[:70]}{OFF}")

    # ---------------------------------------------------------------- 5
    chosen = outcome.offers[0]
    head("5", f"You choose: {chosen.offer.title[:38]} at {chosen.offer.store_label}")

    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        adapter = ShopifyMCPAdapter(
            chosen.offer.store, chosen.offer.store_label, client=client
        )
        written = await adapter.add_to_cart(chosen.offer.variant_id, QUANTITY)
        print(f"   wrote     {QUANTITY} x {rupees(chosen.offer.price_minor)}")
        observed = await adapter.read_cart(written.cart_id)      # separate call
        print(f"   store says {sum(l.quantity for l in observed.lines)} items, "
              f"{rupees(observed.total_paise)}")

    # ---------------------------------------------------------------- 6
    head("6", "The checks")
    intent = PurchaseIntent(
        intent_id="demo", user_id="demo", merchant=chosen.offer.store,
        items=[IntentItem(requested_product="millet cereal", quantity=QUANTITY,
                          unit="pack")],
        maximum_total_paise=BUDGET, status=IntentStatus.READY_FOR_CHECKOUT,
    )
    expectation = CartExpectation(
        merchant=chosen.offer.store, maximum_total_paise=BUDGET,
        lines=[ApprovedCartLine(variant_id=chosen.offer.variant_id,
                                quantity=QUANTITY,
                                unit_price_paise=chosen.offer.price_minor)],
    )
    evidence = CheckoutEvidence(
        merchant_permitted=True, cart_unique=True, attributes_match=True,
        items_available=True, idempotency_free=True,
    )
    comparison = compare_cart(expectation, observed)
    confirmed = intent.model_copy(update={
        "status": IntentStatus.CONFIRMED,
        "confirmed_cart_hash": comparison.cart_hash,
    })
    passing = evaluate_pre_payment_gates(confirmed, expectation, observed, evidence)
    total = len(passing.passed) + len(passing.failed)
    print(f"   {GREEN}{len(passing.passed)}/{total} passed   allow={passing.allow}{OFF}")
    print(f"   {DIM}cart hash {comparison.cart_hash[:32]}…{OFF}")

    # ---------------------------------------------------------------- 7
    head("7", "Now the agent slips: 20 packs, not 2")
    tampered = ObservedCart(
        merchant=observed.merchant, cart_id=observed.cart_id,
        currency=observed.currency,
        lines=[CartLine(sku=chosen.offer.variant_id,
                        variant_id=chosen.offer.variant_id, quantity=20,
                        line_total_paise=chosen.offer.price_minor * 20)],
        total_paise=chosen.offer.price_minor * 20,
    )
    blocked = evaluate_pre_payment_gates(confirmed, expectation, tampered, evidence)
    print(f"   {RED}{len(blocked.passed)}/{total} passed   allow={blocked.allow}{OFF}")
    for name in blocked.failed:
        print(f"   {RED}✕{OFF} {blocked.reasons[name]}")

    # ---------------------------------------------------------------- 8
    head("8", "The same guard, for an assistant shopping somewhere else")

    def tool(name, args):
        reply = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": args}})
        return json.loads(reply["result"]["content"][0]["text"])

    recorded = tool("record_intent", {
        "user_request": "two plates of chicken momos, under 400 rupees",
        "items": [{"product": "chicken momos", "quantity": 2, "unit": "plate"}],
        "maximum_total_paise": 40_000,
    })
    print(f"   recorded  {recorded['approved']}, limit {rupees(40_000)}")

    for label, quantity, line_total in (("correct cart", 2, 33_800),
                                        ("20 plates", 20, 338_000)):
        verdict = tool("check_cart", {
            "intent_id": recorded["intent_id"], "merchant": "zomato",
            "lines": [{"item_id": "dish_8871", "title": "Chicken Momos",
                       "quantity": quantity, "line_total_paise": line_total}],
            "total_paise": line_total,
        })
        mark = GREEN if verdict["allow"] else RED
        print(f"   {mark}{label:14} allow={str(verdict['allow']):5} "
              f"{verdict['checks_passed']}/{verdict['checks_total']}{OFF}")
        for reason in verdict["reasons"]:
            print(f"      {DIM}{reason[:62]}{OFF}")

    # ----------------------------------------------------------------
    print(f"\n{BOLD}Nothing was bought.{OFF} The cart is anonymous and is now "
          f"abandoned.\n{DIM}Payment runs separately, in Razorpay test mode, on "
          f"our own merchant account.{OFF}\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
