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

from .cart_verifier import ApprovedCartLine, CartExpectation
from .checkout_guard import ConfirmationResult, confirm_cart, ready_for_checkout
from .commerce import GROCERY, Offer, SearchOutcome, ShopifyMCPAdapter, search_stores
from .commerce.discovery import DiscoveryRefused, discover
from .commerce.stores import ALL as ALL_STORES, Store
from .connectors import CONNECTORS, summary as connector_summary
from .intent_compiler import CompilationResult, compile_intent
from .llm import provider_from_env
from .mcp_server import router as mcp_router
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
    remember_store,
    saved_stores,
    set_preference,
    suggest_reorder,
)
from .models import ObservedCart, PurchaseIntent

app = FastAPI(title="OrderGuard", version="0.1.0")

# One database for chat, order history and preferences. Opened once; the module
# owns it so no request handler can point memory somewhere else.
MEMORY = memory_engine()

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


class ContinueSessionRequest(BaseModel):
    model_config = STRICT

    message: str = Field(min_length=1, max_length=2_000)


class SelectOfferRequest(BaseModel):
    model_config = STRICT

    offer_key: str = Field(min_length=1)
    explicit_user_selection: Literal[True]


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
def create_session(request: CreateSessionRequest) -> ShoppingSession:
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

    session = ShoppingSession(
        session_id=session_id,
        user_id=request.user_id,
        request_text=request.request_text,
        intent=result.intent,
        clarifications=[question.question for question in result.clarifications],
        memory_notes=notes,
    )
    _SESSIONS[session_id] = session
    return session


@app.post("/api/sessions/{session_id}/messages", response_model=ShoppingSession)
def continue_session(
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
    session.request_text = f"{session.request_text}\nUser clarification: {request.message}"
    result = compile_intent(
        provider_from_env(),
        user_request=session.request_text,
        intent_id=session.intent.intent_id if session.intent else f"intent_{session_id}",
        user_id=session.user_id,
    )
    session.intent = result.intent
    session.clarifications = [question.question for question in result.clarifications]
    session.offers_by_item.clear()
    session.selected_by_item.clear()
    session.observed_cart = None
    session.confirmation = None
    return session


@app.post("/api/sessions/{session_id}/items/{item_index}/search", response_model=SearchOutcome)
async def search_item(session_id: str, item_index: int) -> SearchOutcome:
    session = _session(session_id)
    if session.intent is None:
        raise HTTPException(status_code=409, detail="answer the clarification before searching")
    if not 0 <= item_index < len(session.intent.items):
        raise HTTPException(status_code=404, detail="unknown requested item")

    item = session.intent.items[item_index]

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
    searchable = GROCERY + added

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
    )
    session.offers_by_item[item_index] = {
        _offer_key(scored.offer): scored.offer for scored in outcome.offers
    }
    return outcome


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
    adapter = ShopifyMCPAdapter(offer.store, offer.store_label)
    quantity = session.intent.items[item_index].quantity
    async with adapter:
        written = await adapter.add_to_cart(offer.variant_id, quantity, cart_id)
        # Separate request: writes are never treated as evidence of what the
        # merchant ultimately put in the cart.
        observed = await adapter.read_cart(written.cart_id)

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
