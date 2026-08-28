"""OrderGuard's real guarded-cart API.

This is a usable local application backend, not a fake success screen. It can
compile a request, search selected Shopify stores, write a cart only after an
explicit offer selection, independently read it back, and freeze a matching
cart. Third-party checkout is deliberately outside this service's authority.
"""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .cart_verifier import ApprovedCartLine, CartExpectation, compare_cart
from .checkout_guard import CheckoutEvidence, ConfirmationResult, confirm_cart, evaluate_pre_payment_gates, ready_for_checkout
from .commerce import (
    AdapterError,
    FreshCartAdapter,
    Location,
    Offer,
    ScoredOffer,
    SearchOutcome,
    ShopifyMCPAdapter,
    search_stores,
)
from .commerce.discovery import DiscoveryRefused, discover
from .commerce.stores import ALL as ALL_STORES, Store, for_query
from .connectors import CONNECTORS, summary as connector_summary
from .intent_compiler import CompilationResult, compile_intent, label_answer
from .llm import provider_from_env
from .mcp_server import router as mcp_router
from .ledger import (
    LedgerStatus,
    attach_order,
    claim_order,
    finalize_if_pending,
    get_entry,
    ledger_engine,
    reject as reject_ledger_entry,
)
from .merchants import Reach, resolve_merchant
from .memory import (
    apply_preferences_to_gaps,
    chat_history,
    forget_everything,
    forget_preference,
    memory_engine,
    preferences,
    recent_orders,
    forget_store,
    remember_chat_turn,
    remember_completed_order,
    remember_store,
    saved_stores,
    set_preference,
    suggest_reorder,
)
from .models import ObservedCart, PurchaseIntent
from .payment import Rejection as PaymentRejection, VerifiedPayment, verify_payment
from .razorpay_client import RazorpayClient, RazorpayError, client_from_env
from .websearch import WebResult, WebSearchOutcome, search_web

app = FastAPI(title="OrderGuard", version="0.1.0")

# One database for chat, order history and preferences. Opened once; the module
# owns it so no request handler can point memory somewhere else.
MEMORY = memory_engine()

# The idempotency ledger. Separate database from MEMORY on purpose: this one is
# safety-critical and append-only in effect (see ledger.py), and keeping it out
# of the same file as chat history and preferences means a bug in one cannot
# corrupt the other.
LEDGER = ledger_engine()

# OrderGuard as an MCP server: any assistant can hand us a cart from any
# connector it already has, and get back a verdict. See mcp_server.py.
app.include_router(mcp_router)

# A dependency-free browser client for the same API. Keeping the client small
# makes the workflow easy to inspect while the product is being built.
app.mount("/app-assets", StaticFiles(directory="web"), name="app-assets")

STRICT = ConfigDict(extra="forbid")


class CreateSessionRequest(BaseModel):
    model_config = STRICT

    user_id: str = Field(min_length=1)
    request_text: str = Field(min_length=1, max_length=2_000)
    # Where to deliver. Sent to stores as a hint and shown to the user; never
    # used in a safety check, since a merchant controls what it returns for it.
    postal_code: str = Field(default="", max_length=12)
    region: str = Field(default="", max_length=8)
    city: str = Field(default="", max_length=64)


class ContinueSessionRequest(BaseModel):
    model_config = STRICT

    message: str = Field(min_length=1, max_length=2_000)


class SelectOfferRequest(BaseModel):
    model_config = STRICT

    offer_key: str = Field(min_length=1)
    explicit_user_selection: Literal[True]


class ItemSearch(SearchOutcome):
    """Store offers, plus the web when no shop we can buy from stocks the item.

    The five shops OrderGuard can transact with are speciality D2C brands —
    millet food, coffee, ghee. Ask them for onions or momos and they correctly
    return nothing, which left the user staring at "No usable options" with no
    way forward (F-021).

    So when the shops come back empty, we look on the open web and hand back
    links. Those are still not purchasable here — same rule as everywhere else —
    but "none of my shops sell this, here is where the web says to get it" is an
    answer, and a blank panel is not.
    """

    web: list[WebResult] = Field(default_factory=list)
    explanation: str = ""
    web_budget_note: str = ""


class ShoppingSession(BaseModel):
    """Server-owned state. No client may provide an observed cart or cart hash."""

    model_config = STRICT

    session_id: str
    user_id: str
    request_text: str = ""
    intent: PurchaseIntent | None = None
    clarifications: list[str] = Field(default_factory=list)
    offers_by_item: dict[int, dict[str, Offer]] = Field(default_factory=dict)
    selected_by_item: dict[int, Offer] = Field(default_factory=dict)
    observed_cart: ObservedCart | None = None
    confirmation: ConfirmationResult | None = None
    # Plain sentences about which remembered values were used. Shown to the
    # user, because memory applied silently is memory they cannot correct.
    memory_notes: list[str] = Field(default_factory=list)
    location: Location | None = None
    # The fields we last asked about, so a bare answer like "2" can be bound to
    # the question it answers instead of being re-parsed out of a text blob.
    pending_fields: list[str] = Field(default_factory=list)
    named_merchant: str = ""
    # Set when the user named a shop we cannot use. Searching anyway would
    # answer a question they did not ask.
    blocked_merchant: str = ""


_SESSIONS: dict[str, ShoppingSession] = {}


def _session(session_id: str) -> ShoppingSession:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return session


def _offer_key(offer: Offer) -> str:
    return f"{offer.store}|{offer.variant_id}"


def _expectation(session: ShoppingSession) -> CartExpectation:
    if session.intent is None or not session.selected_by_item:
        raise HTTPException(status_code=409, detail="select every requested item first")
    if len(session.selected_by_item) != len(session.intent.items):
        raise HTTPException(status_code=409, detail="select every requested item first")

    stores = {offer.store for offer in session.selected_by_item.values()}
    if len(stores) != 1:
        raise HTTPException(
            status_code=409,
            detail="one guarded cart may contain products from only one merchant",
        )

    return CartExpectation(
        merchant=next(iter(stores)),
        currency=session.intent.currency,
        maximum_total_paise=session.intent.maximum_total_paise,
        lines=[
            ApprovedCartLine(
                variant_id=offer.variant_id,
                quantity=session.intent.items[index].quantity,
                # the price on screen when this offer was chosen, carried through
                # so the cart can be checked against it rather than only the cap
                unit_price_paise=offer.price_minor,
            )
            for index, offer in sorted(session.selected_by_item.items())
        ],
    )


@app.post("/api/sessions", response_model=ShoppingSession)
async def create_session(request: CreateSessionRequest) -> ShoppingSession:
    session_id = str(uuid4())
    remember_chat_turn(
        MEMORY, session_id=session_id, user_id=request.user_id,
        role="user", text=request.request_text,
    )

    result: CompilationResult = compile_intent(
        provider_from_env(),
        user_request=request.request_text,
        intent_id=f"intent_{session_id}",
        user_id=request.user_id,
    )

    # Memory fills gaps only. A store the user named in this request is already
    # on the intent and wins; a remembered one is used only when they said
    # nothing, and either way it is announced below.
    stated = {"store": result.intent.merchant} if result.intent else {}
    _, notes = apply_preferences_to_gaps(
        stated, preferences(MEMORY, request.user_id, session_id=session_id)
    )

    location = Location(
        postal_code=request.postal_code, region=request.region, city=request.city
    )
    if location.postal_code or location.city:
        notes.append(f"Delivering to {location.described}.")

    session = ShoppingSession(
        session_id=session_id,
        user_id=request.user_id,
        request_text=request.request_text,
        intent=result.intent,
        clarifications=[question.question for question in result.clarifications],
        pending_fields=[question.field for question in result.clarifications],
        named_merchant=result.draft_merchant,
        memory_notes=notes,
        location=location,
    )
    await _check_named_shop(session)
    _SESSIONS[session_id] = session
    return session


async def _check_named_shop(session: ShoppingSession) -> None:
    """If the user named a shop, find out now whether we can use it.

    Before this existed, "order 2 pizza from La Pinoz" asked for a budget,
    searched five grocery stores and offered a mozzarella block. Every step
    worked; the answer was nonsense. See F-015.
    """
    named = session.named_merchant or (
        session.intent.merchant if session.intent else ""
    )
    if not named:
        return

    verdict = await resolve_merchant(
        named,
        extra_domains=tuple(
            (s.domain, s.label) for s in saved_stores(MEMORY, session.user_id)
        ),
    )
    if verdict.can_shop:
        session.memory_notes.append(verdict.message)
        return

    session.blocked_merchant = verdict.named
    session.clarifications = [verdict.message]
    session.pending_fields = ["merchant"]


@app.post("/api/sessions/{session_id}/messages", response_model=ShoppingSession)
async def continue_session(
    session_id: str, request: ContinueSessionRequest
) -> ShoppingSession:
    """Add an answer to the same conversation and recompile its request.

    This route only advances planning. It does not search, alter a cart, or
    call payment, which lets the UI pause safely while the user asks questions.
    """
    session = _session(session_id)
    remember_chat_turn(
        MEMORY, session_id=session_id, user_id=session.user_id,
        role="user", text=request.message,
    )
    # Bind the reply to the question it answers. A bare "2" appended to a
    # request that already says "under 400" is ambiguous, and the model kept
    # re-asking the same question (F-014).
    field = session.pending_fields[0] if session.pending_fields else ""
    session.request_text = f"{session.request_text}\n{label_answer(field, request.message)}"
    result = compile_intent(
        provider_from_env(),
        user_request=session.request_text,
        intent_id=session.intent.intent_id if session.intent else f"intent_{session_id}",
        user_id=session.user_id,
    )
    session.intent = result.intent
    session.clarifications = [question.question for question in result.clarifications]
    session.pending_fields = [question.field for question in result.clarifications]
    session.named_merchant = result.draft_merchant
    session.blocked_merchant = ""
    session.offers_by_item.clear()
    session.selected_by_item.clear()
    session.observed_cart = None
    session.confirmation = None
    await _check_named_shop(session)
    return session


@app.post("/api/sessions/{session_id}/items/{item_index}/search", response_model=ItemSearch)
async def search_item(session_id: str, item_index: int) -> ItemSearch:
    session = _session(session_id)
    if session.intent is None:
        raise HTTPException(status_code=409, detail="answer the clarification before searching")
    if session.blocked_merchant:
        raise HTTPException(
            status_code=409,
            detail=session.clarifications[0] if session.clarifications
            else f"I cannot shop {session.blocked_merchant}.",
        )
    if not 0 <= item_index < len(session.intent.items):
        raise HTTPException(status_code=404, detail="unknown requested item")

    item = session.intent.items[item_index]

    # FreshCart is ours — it is where the Razorpay test payment actually runs
    # (D-020: a Shopify store collects its own money, we never can). It is
    # opt-in by name only, never mixed into a blind search across every real
    # store, because its catalogue is synthetic and the rest are not.
    if session.intent.merchant.strip().lower() == "freshcart":
        async with FreshCartAdapter() as fc:
            offers = await fc.search(item.requested_product, limit=8)
        outcome = SearchOutcome(
            query=item.requested_product, quantity=item.quantity,
            budget_minor=session.intent.maximum_total_paise,
            offers=[
                ScoredOffer(
                    offer=offer, relevance=1.0, in_stock=offer.available, priced=True,
                    within_budget=offer.total_minor(item.quantity) <= session.intent.maximum_total_paise,
                    line_total_minor=offer.total_minor(item.quantity),
                )
                for offer in offers
            ],
            stores_searched=["FreshCart"],
        )
        session.offers_by_item[item_index] = {
            _offer_key(scored.offer): scored.offer for scored in outcome.offers
        }
        result = ItemSearch(**outcome.model_dump())
        if not outcome.offers:
            result.explanation = (
                f"FreshCart does not stock {item.requested_product}. "
                f"Try a different item, or ask for a real store instead."
            )
        return result

    # A store the user named, or one they told us to remember, narrows the
    # search. Anything else searches everywhere. Narrowing is announced on the
    # session; it never happens silently.
    wanted = session.intent.merchant or preferences(
        MEMORY, session.user_id, session_id=session_id
    ).get("store", "")

    # Stores the user added themselves are searched alongside ours. This is what
    # makes the catalogue grow by use rather than by us maintaining a list.
    added = tuple(
        Store(domain=s.domain, label=s.label, kind="added")
        for s in saved_stores(MEMORY, session.user_id)
    )
    searchable = for_query(item.requested_product) + added

    stores = searchable
    if wanted:
        matched = tuple(
            s for s in ALL_STORES + added
            if wanted.lower() in {s.domain.lower(), s.label.lower()}
        )
        if matched:
            stores = matched

    outcome = await search_stores(
        item.requested_product,
        quantity=item.quantity,
        budget_minor=session.intent.maximum_total_paise,
        stores=stores,
        location=session.location,
    )
    session.offers_by_item[item_index] = {
        _offer_key(scored.offer): scored.offer for scored in outcome.offers
    }

    result = ItemSearch(**outcome.model_dump())
    if outcome.offers:
        return result

    shops = ", ".join(outcome.stores_searched) or "the stores I can reach"
    found = await search_web(
        item.requested_product,
        quantity=item.quantity,
        budget_paise=session.intent.maximum_total_paise or None,
    )
    result.web = found.results
    result.web_budget_note = found.budget_note

    if result.web:
        result.explanation = (
            f"None of the shops I can buy from sell {item.requested_product}. "
            f"I searched {shops}. Here is what the web shows — you can open "
            f"these yourself; I cannot add them to a cart. "
            f"{found.budget_note}"
        ).strip()
    elif outcome.suggestions:
        result.explanation = (
            f"I could not find {item.requested_product} at {shops}. "
            f"They do sell: {', '.join(outcome.suggestions[:3])}."
        )
    else:
        result.explanation = (
            f"I searched {shops} and found no {item.requested_product}. "
            f"{found.unavailable_reason}".strip()
        )
    return result


@app.post("/api/sessions/{session_id}/items/{item_index}/select", response_model=ShoppingSession)
async def select_offer(
    session_id: str, item_index: int, request: SelectOfferRequest
) -> ShoppingSession:
    session = _session(session_id)
    if session.intent is None:
        raise HTTPException(status_code=409, detail="answer the clarification before selecting")
    if item_index not in session.offers_by_item:
        raise HTTPException(status_code=409, detail="search this item before selecting it")
    offer = session.offers_by_item[item_index].get(request.offer_key)
    if offer is None:
        raise HTTPException(status_code=404, detail="offer is not part of this session's search results")
    if not offer.available:
        raise HTTPException(status_code=409, detail="selected offer is no longer available")

    # If the shopper named a store, honour it. Matching is exact against the
    # domain or the label, both lowercased. Nothing fuzzy: "blue" must not be
    # allowed to select Blue Tokai when the user meant something else.
    named = session.intent.merchant.strip().lower()
    if named and named not in {offer.store.lower(), offer.store_label.lower()}:
        raise HTTPException(
            status_code=409,
            detail=f"you asked to shop at {session.intent.merchant}; this offer is from {offer.store_label}",
        )

    existing_stores = {chosen.store for chosen in session.selected_by_item.values()}
    if existing_stores and offer.store not in existing_stores:
        raise HTTPException(
            status_code=409,
            detail="choose offers from one merchant for this cart",
        )

    cart_id = session.observed_cart.cart_id if session.observed_cart else None
    quantity = session.intent.items[item_index].quantity
    adapter = (
        FreshCartAdapter() if offer.store == "freshcart"
        else ShopifyMCPAdapter(offer.store, offer.store_label)
    )
    # FreshCart is one merchant with one cart per session; our own carts have
    # no per-store key collision to worry about, so a stable id per session is
    # enough rather than whatever cart_id a prior Shopify store handed back.
    if offer.store == "freshcart":
        cart_id = f"orderguard-{session_id}"
    try:
        async with adapter:
            written = await adapter.add_to_cart(offer.variant_id, quantity, cart_id)
            # Separate request: writes are never treated as evidence of what the
            # merchant ultimately put in the cart.
            observed = await adapter.read_cart(written.cart_id)
    except AdapterError as exc:
        # Fail closed, on purpose, with a message instead of a stack trace.
        # Before this, a store going down mid-write reached FastAPI as an
        # unhandled exception: no cart was touched, so nothing unsafe
        # happened, but the user saw a raw 500 with no explanation instead of
        # a plain refusal. "Fails closed" by accident is not the same claim as
        # "fails closed, and says so" (F-028).
        raise HTTPException(
            status_code=502,
            detail=f"{offer.store_label} did not respond while adding this to the "
                   f"cart. Nothing was changed. {exc}",
        ) from exc

    session.selected_by_item[item_index] = offer
    session.observed_cart = observed
    session.confirmation = None
    return session


@app.post("/api/sessions/{session_id}/confirm", response_model=ConfirmationResult)
def confirm_session_cart(session_id: str) -> ConfirmationResult:
    session = _session(session_id)
    if session.intent is None or session.observed_cart is None:
        raise HTTPException(status_code=409, detail="select and read a cart before confirmation")

    expectation = _expectation(session)
    selected_intent = session.intent.model_copy(update={"merchant": expectation.merchant})
    result = confirm_cart(ready_for_checkout(selected_intent), expectation, session.observed_cart)
    session.confirmation = result
    if result.intent is not None:
        session.intent = result.intent
    return result


@app.get("/api/sessions/{session_id}", response_model=ShoppingSession)
def get_session(session_id: str) -> ShoppingSession:
    return _session(session_id)


# --- payment: Razorpay test mode, verified server-side, exactly once -------

class PaymentOrderResponse(BaseModel):
    """Everything the browser needs to open Razorpay Checkout, and nothing more.

    No secret ever appears here. ``key_id`` is the public half of the pair —
    Razorpay's own documentation has it embedded directly in front-end code.
    """

    model_config = STRICT

    key_id: str
    razorpay_order_id: str
    amount_paise: int
    currency: str
    status: str                    # "pending" | "captured" — ledger state
    gates_passed: int
    gates_total: int


class PaymentVerifyRequest(BaseModel):
    """What ``checkout.js``'s handler receives. The order id is deliberately
    NOT accepted here — it is read from our own ledger row for this session,
    so a payment cannot be verified against an order it does not belong to."""

    model_config = STRICT

    razorpay_payment_id: str = Field(min_length=1)
    razorpay_signature: str = Field(min_length=1)


class PaymentVerifyResponse(BaseModel):
    model_config = STRICT

    captured: bool
    payment_id: str = ""
    amount_paise: int = 0
    reason: str = ""
    # True when this call found the purchase ALREADY captured by an earlier
    # call. This is what "70 duplicate events -> exactly 1 business effect"
    # looks like from the outside: 70 successful-looking responses, one write.
    already_captured: bool = False


def _idempotency_key(session: ShoppingSession) -> str:
    """merchant | purchase_intent_id | action_type | cart_hash — frozen once
    at confirmation (D-004), so a retried purchase always maps to one row."""
    intent = session.intent
    if intent is None or not intent.confirmed_cart_hash:
        raise HTTPException(status_code=409, detail="confirm the cart before paying")
    return f"{intent.merchant}|{intent.intent_id}|purchase|{intent.confirmed_cart_hash}"


def _known_merchant_domains(user_id: str) -> set[str]:
    return (
        {s.domain for s in ALL_STORES}
        | {s.domain for s in saved_stores(MEMORY, user_id)}
        | {"freshcart"}
    )


def _pre_payment_gates(session: ShoppingSession):
    """Run all twelve named gates with real evidence, not a rubber stamp.

    This is the step the running app skipped until now: ``confirm_cart``
    freezes a hash, but nothing had actually evaluated MERCHANT_PERMITTED,
    CART_UNIQUE, ATTRIBUTES_MATCH, ITEMS_AVAILABLE or IDEMPOTENCY_FREE before a
    payment could be requested.
    """
    expectation = _expectation(session)
    observed = session.observed_cart
    assert observed is not None and session.intent is not None    # guarded by caller

    key = _idempotency_key(session)
    existing = get_entry(LEDGER, key)

    evidence = CheckoutEvidence(
        merchant_permitted=expectation.merchant in _known_merchant_domains(session.user_id),
        cart_unique=True,          # _expectation() already refuses more than one merchant
        # No product attribute data reaches this layer yet (Offer carries no
        # attribute map). Rather than assume a match we cannot see, the gate
        # passes only when the user asked for none — an honest limitation,
        # not a guess.
        attributes_match=not any(item.required_attributes for item in session.intent.items),
        items_available=bool(observed.lines) and all(line.quantity > 0 for line in observed.lines),
        idempotency_free=existing is None or existing.status is not LedgerStatus.CAPTURED,
    )
    return evaluate_pre_payment_gates(session.intent, expectation, observed, evidence), expectation


@app.post("/api/sessions/{session_id}/payment/order", response_model=PaymentOrderResponse)
async def create_payment_order(session_id: str) -> PaymentOrderResponse:
    """Headless half of the payment path. No browser needed for this call.

    Idempotent: calling this twice for the same confirmed cart returns the
    SAME Razorpay order rather than creating a second one.
    """
    session = _session(session_id)
    if session.intent is None or session.observed_cart is None or session.confirmation is None \
            or not session.confirmation.confirmed:
        raise HTTPException(status_code=409, detail="confirm the cart before starting payment")

    gates, expectation = _pre_payment_gates(session)
    if not gates.allow:
        raise HTTPException(
            status_code=409,
            detail={
                "reasons": [gates.reasons[name] for name in gates.failed],
                "failed_gates": [str(name) for name in gates.failed],
            },
        )

    key_id, key_secret = client_from_env()
    key = _idempotency_key(session)

    entry, created = claim_order(
        LEDGER, idempotency_key=key, merchant=expectation.merchant,
        purchase_intent_id=session.intent.intent_id,
        cart_hash=session.intent.confirmed_cart_hash or "",
        expected_amount_paise=session.observed_cart.total_paise,
        currency=expectation.currency,
    )

    if created:
        try:
            async with RazorpayClient(key_id, key_secret) as rzp:
                order = await rzp.create_order(
                    amount_paise=entry.expected_amount_paise, currency=entry.currency,
                    receipt=key, notes={"purchase_intent_id": session.intent.intent_id},
                )
        except RazorpayError as exc:
            raise HTTPException(
                status_code=502, detail=f"could not create the Razorpay order: {exc}"
            ) from exc
        attach_order(LEDGER, key, str(order["id"]))
        entry = get_entry(LEDGER, key) or entry

    return PaymentOrderResponse(
        key_id=key_id,
        razorpay_order_id=entry.razorpay_order_id,
        amount_paise=entry.captured_amount_paise or entry.expected_amount_paise,
        currency=entry.currency,
        status=entry.status.value,
        gates_passed=len(gates.passed),
        gates_total=len(gates.passed) + len(gates.failed),
    )


@app.post("/api/sessions/{session_id}/payment/verify", response_model=PaymentVerifyResponse)
async def verify_session_payment(
    session_id: str, request: PaymentVerifyRequest
) -> PaymentVerifyResponse:
    """The only path that may mark a purchase complete.

    Never trusts ``checkout.js``'s own success message — see payment.py.
    Safe to call any number of times: after the first successful call, every
    later one (a retry, a duplicate webhook, 70 identical requests) finds the
    row already captured and returns that same original result without
    re-verifying or writing to memory a second time.
    """
    session = _session(session_id)
    if session.intent is None:
        raise HTTPException(status_code=409, detail="no confirmed purchase to verify")

    key = _idempotency_key(session)
    entry = get_entry(LEDGER, key)
    if entry is None:
        raise HTTPException(status_code=409, detail="create the payment order first")

    if entry.status is LedgerStatus.CAPTURED:
        return PaymentVerifyResponse(
            captured=True, payment_id=entry.razorpay_payment_id,
            amount_paise=entry.captured_amount_paise or 0, already_captured=True,
        )

    key_id, key_secret = client_from_env()
    async with RazorpayClient(key_id, key_secret) as rzp:
        result = await verify_payment(
            order_id=entry.razorpay_order_id,
            payment_id=request.razorpay_payment_id,
            signature=request.razorpay_signature,
            key_secret=key_secret,
            client=rzp,
            expected_amount_paise=entry.expected_amount_paise,
            expected_currency=entry.currency,
        )

    if isinstance(result, PaymentRejection):
        reject_ledger_entry(LEDGER, key, result.reason)
        return PaymentVerifyResponse(captured=False, reason=result.reason)

    updated, won = finalize_if_pending(
        LEDGER, idempotency_key=key,
        razorpay_payment_id=result.payment_id, captured_amount_paise=result.amount_paise,
    )
    assert updated is not None

    if won:
        # The ONLY path into order history (memory.py), and it runs at most
        # once per purchase — guarded by the same claim that just resolved.
        for index, offer in session.selected_by_item.items():
            remember_completed_order(
                MEMORY, user_id=session.user_id, payment_id=result.payment_id,
                store=offer.store, store_label=offer.store_label,
                variant_id=offer.variant_id, title=offer.title,
                quantity=session.intent.items[index].quantity,
                unit_price_paise=offer.price_minor,
                requested_as=session.intent.items[index].requested_product,
            )

    return PaymentVerifyResponse(
        captured=True, payment_id=updated.razorpay_payment_id,
        amount_paise=updated.captured_amount_paise or 0, already_captured=not won,
    )


@app.get("/app/pay/{session_id}", include_in_schema=False)
def payment_page(session_id: str) -> FileResponse:
    """The Razorpay Checkout page. Session id is in the URL only to load the
    right cart on open — the amount and order id always come from the ledger,
    never from anything the page itself could claim."""
    return FileResponse("web/checkout.html")




# --- connectors ------------------------------------------------------------

@app.get("/api/connectors")
def list_connectors() -> dict:
    """Every commerce surface we know of, with its real status and evidence.

    Deliberately returns the blocked ones too. "Swiggy is real but we cannot
    reach it, here is the HTTP code we got" is more useful, and more honest,
    than a list showing only what happens to work.
    """
    return {
        "summary": connector_summary(),
        "connectors": [c._asdict() for c in CONNECTORS],
    }


@app.post("/api/sessions/{session_id}/items/{item_index}/web")
async def search_the_web(session_id: str, item_index: int) -> WebSearchOutcome:
    """Look at shops we cannot buy from, so the comparison is honest.

    Amazon, Flipkart and Myntra have no agent surface we may use. Pretending
    they do not exist makes the price comparison worse for no gain, so we search
    them and show what we find — as links to open, never as things to add.

    Kept separate from the store search on purpose: one returns offers a cart
    can be built from, the other returns claims. Merging them into one list
    would be the first step towards treating them the same.
    """
    session = _session(session_id)
    if session.intent is None:
        raise HTTPException(status_code=409, detail="answer the clarification first")
    if not 0 <= item_index < len(session.intent.items):
        raise HTTPException(status_code=404, detail="unknown requested item")

    item = session.intent.items[item_index]
    return await search_web(
        item.requested_product,
        quantity=item.quantity,
        budget_paise=session.intent.maximum_total_paise or None,
    )


# --- shopping at a store nobody integrated ---------------------------------

class DiscoverStoreRequest(BaseModel):
    model_config = STRICT

    domain: str = Field(min_length=1, max_length=253)


@app.post("/api/users/{user_id}/stores")
async def add_store(user_id: str, request: DiscoverStoreRequest) -> dict:
    """Point OrderGuard at any shop and find out whether it can be used.

    The store list is not something we maintain. Every Shopify storefront
    answers at /api/mcp, so a shop this project has never heard of works the
    moment someone names it. Verified shops are saved and searched from then on.
    """
    try:
        found = await discover(request.domain)
    except DiscoveryRefused as exc:
        # Refusals include "that is this machine". Passing the reason through is
        # the point; a bare 400 would look like the shop was merely offline.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if found.shoppable:
        remember_store(
            MEMORY, user_id=user_id, domain=found.domain, tools=found.tools
        )

    return {
        "domain": found.domain,
        "shoppable": found.shoppable,
        "can_search": found.can_search,
        "can_cart": found.can_cart,
        "tools": list(found.tools),
        "saved": found.shoppable,
        "message": found.summary,
    }


@app.get("/api/users/{user_id}/stores")
def list_saved_stores(user_id: str) -> dict:
    return {
        "verified_by_us": [
            {"domain": s.domain, "label": s.label, "kind": s.kind} for s in ALL_STORES
        ],
        "added_by_you": [
            {"domain": s.domain, "label": s.label, "added": s.created_at.date().isoformat()}
            for s in saved_stores(MEMORY, user_id)
        ],
    }


@app.delete("/api/users/{user_id}/stores/{domain}")
def remove_saved_store(user_id: str, domain: str) -> dict:
    return {"removed": forget_store(MEMORY, user_id, domain)}


# --- memory ----------------------------------------------------------------

class PreferenceRequest(BaseModel):
    model_config = STRICT

    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    scope: Literal["always", "session"] = "always"
    session_id: str = ""


@app.get("/api/memory/{user_id}")
def read_memory(user_id: str, session_id: str = "") -> dict:
    """What OrderGuard remembers about you, in full.

    Everything it holds is shown. Memory you cannot inspect is memory you
    cannot correct.
    """
    return {
        "preferences": preferences(MEMORY, user_id, session_id=session_id),
        "reorder_suggestion": suggest_reorder(MEMORY, user_id),
        "recent_orders": [
            {
                "title": o.title, "store_label": o.store_label,
                "quantity": o.quantity, "unit_price_paise": o.unit_price_paise,
                "bought_on": o.created_at.date().isoformat(),
            }
            for o in recent_orders(MEMORY, user_id)
        ],
        "note": "Nothing here can raise a spending limit or approve a purchase.",
    }


@app.post("/api/memory/{user_id}/preferences")
def write_preference(user_id: str, request: PreferenceRequest) -> dict:
    try:
        set_preference(
            MEMORY, user_id=user_id, key=request.key, value=request.value,
            scope=request.scope, session_id=request.session_id,
        )
    except ValueError as exc:
        # The refusal text explains WHY a budget is not storable. Passing it
        # through unchanged is the point; a bare 400 would teach nobody.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"preferences": preferences(MEMORY, user_id, session_id=request.session_id)}


@app.delete("/api/memory/{user_id}/preferences/{key}")
def delete_preference(user_id: str, key: str) -> dict:
    return {"removed": forget_preference(MEMORY, user_id, key)}


@app.delete("/api/memory/{user_id}")
def delete_memory(user_id: str) -> dict:
    """Forget everything about this user. Offered plainly, because it must be."""
    return {"deleted": forget_everything(MEMORY, user_id)}


@app.get("/api/sessions/{session_id}/history")
def read_history(session_id: str) -> dict:
    """The conversation, so a reload does not lose the thread."""
    return {
        "turns": [
            {"role": t.role, "text": t.text, "at": t.created_at.isoformat()}
            for t in chat_history(MEMORY, session_id)
        ]
    }


@app.get("/app", include_in_schema=False)
def assistant_app() -> FileResponse:
    """Open the visible OrderGuard assistant client."""
    return FileResponse("web/index.html")
