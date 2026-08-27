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
from .commerce import Offer, SearchOutcome, ShopifyMCPAdapter, search_stores
from .intent_compiler import CompilationResult, compile_intent
from .llm import provider_from_env
from .models import ObservedCart, PurchaseIntent

app = FastAPI(title="OrderGuard", version="0.1.0")

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
    result: CompilationResult = compile_intent(
        provider_from_env(),
        user_request=request.request_text,
        intent_id=f"intent_{session_id}",
        user_id=request.user_id,
    )
    session = ShoppingSession(
        session_id=session_id,
        user_id=request.user_id,
        request_text=request.request_text,
        intent=result.intent,
        clarifications=[question.question for question in result.clarifications],
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
    outcome = await search_stores(
        item.requested_product,
        quantity=item.quantity,
        budget_minor=session.intent.maximum_total_paise,
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


@app.get("/app", include_in_schema=False)
def assistant_app() -> FileResponse:
    """Open the visible OrderGuard assistant client."""
    return FileResponse("web/index.html")
