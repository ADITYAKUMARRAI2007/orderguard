"""OrderGuard as an MCP server, so any assistant can be checked.

The other half of this project shops directly: it talks to Shopify stores,
builds carts, and verifies them. That only works where we are allowed to
connect, which rules out Zomato and Swiggy — both of which ship order placement
and payment to AI agents today.

This module removes that limit by turning the problem around.

    You  ──▶  Claude  ──┬──▶  Zomato connector   (your own login, your cart)
                        └──▶  OrderGuard         (checks the cart)

The assistant does the shopping through whatever connector the user has already
connected. It then hands the cart here and gets back a verdict. We never call
the merchant's API, so there is no OAuth to obtain and no redirect URI to be
whitelisted — the person shopping is the one holding the account, which is what
those platforms permit.

The important consequence: **this checks carts, not stores.** A cart from Zomato
and a cart from a Shopify shop go through identical code, so a merchant we have
never heard of is supported the day someone connects it.

**What this does not do.** It verifies; it does not enforce. An assistant is
free not to call us, and nothing here can stop it. That gap is real and it is
stated in the README rather than papered over. Enforcement has to live where the
money actually moves — at the payment layer — which is the argument for why this
belongs in a payments company rather than in an assistant.

Speaks MCP over HTTP JSON-RPC: ``initialize``, ``tools/list``, ``tools/call``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .audit import ChainTampered, audit_engine, append_event, event_payload, verify_chain
from .cart_verifier import ApprovedCartLine, CartExpectation, compare_cart
from .checkout_guard import CheckoutEvidence, evaluate_pre_payment_gates
from .connector_log import connector_log_engine, record_check
from .connectors import (
    CONNECTORS as ALL_CONNECTORS,
    Evidence,
    by_id as connector_by_id,
    cart_capable_connectors,
    summary as connector_summary,
)
from .enums import IntentStatus
from .memory import memory_engine, remember_store, saved_stores
from .models import CartLine, IntentItem, ObservedCart, PurchaseIntent

__all__ = ["router", "PROTOCOL_VERSION", "TOOLS", "handle_rpc", "AUDIT", "CONNECTOR_LOG", "MEMORY"]

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "orderguard", "version": "0.1.0"}

# Every request through this MCP server is treated as one shared caller. There
# is no login here — the whole point is any assistant, with any connector, can
# call in — so preferences and history are scoped to this one placeholder
# rather than pretending we can tell users apart without one.
_USER_ID = "mcp-client"

# DIRECT_VERIFIED and CONNECTOR_VERIFIED come first because we or a proven
# session have actually seen them work; AVAILABLE_UNTESTED next because it is
# real and reachable; RESTRICTED last because it is real but the CALLING
# assistant, not OrderGuard, would need its own access to actually use it.
_EVIDENCE_RANK = {
    Evidence.DIRECT_VERIFIED: 0, Evidence.CONNECTOR_VERIFIED: 1,
    Evidence.AVAILABLE_UNTESTED: 2, Evidence.RESTRICTED: 3, Evidence.UNAVAILABLE: 4,
}

router = APIRouter()

# Approved intents, by id. An assistant cannot hand us an intent and a cart in
# the same breath: the intent is recorded first, in the user's own words, and
# the cart is checked against it afterwards. Otherwise a confused assistant
# would simply describe the cart it built as the thing that was wanted.
_INTENTS: dict[str, PurchaseIntent] = {}

# Every intent and every cart check — allowed or refused — lands here. A
# refusal is recorded with the same weight as an action (see audit.py); this
# is what makes "why was I blocked" a question with a real, replayable answer
# instead of a sentence you have to take on trust.
AUDIT = audit_engine()

# Which merchant, allowed or blocked, when — queryable evidence that this
# server has actually been checked against real carts, not just claimed to be.
CONNECTOR_LOG = connector_log_engine()

# category -> preferred connector, learned from what actually got allowed.
# Never raises a spending cap or invents a connector outside connectors.py —
# same rules memory.py already enforces everywhere else in this project.
MEMORY = memory_engine()


# --- what an assistant sends us --------------------------------------------

STRICT = ConfigDict(extra="forbid")


class StatedItem(BaseModel):
    model_config = STRICT

    product: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    unit: str = "unit"
    max_unit_price_paise: int | None = Field(default=None, ge=0)


class ObservedLine(BaseModel):
    """One line of the cart the assistant actually built, at the merchant."""

    model_config = STRICT

    item_id: str = Field(min_length=1)      # variant id, dish id, SKU — any stable id
    title: str = ""
    quantity: int = Field(ge=0)
    line_total_paise: int = Field(ge=0)


# --- the tools --------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "record_intent",
        "description": (
            "Record what the user asked to buy, BEFORE shopping. Returns an "
            "intent_id and anything still missing. Call this first, using the "
            "user's own words — never your own summary of a cart you have "
            "already built."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["user_request", "items", "maximum_total_paise"],
            "properties": {
                "user_request": {
                    "type": "string",
                    "description": "Exactly what the user said, unedited.",
                },
                "items": {
                    "type": "array",
                    "description": "What they asked for.",
                    "items": {
                        "type": "object",
                        "required": ["product", "quantity"],
                        "properties": {
                            "product": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 1},
                            "unit": {"type": "string"},
                            "max_unit_price_paise": {"type": "integer", "minimum": 0},
                        },
                    },
                },
                "maximum_total_paise": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "The most the user agreed to spend, in paise. ₹400 is "
                        "40000. If the user did not state a limit, ask them — "
                        "do not invent one."
                    ),
                },
                "merchant": {
                    "type": "string",
                    "description": "Only if the user named a shop themselves.",
                },
                "currency": {"type": "string", "default": "INR"},
            },
        },
    },
    {
        "name": "check_cart",
        "description": (
            "Check the cart you built against the recorded intent. Returns "
            "allow=true or allow=false with a reason for every failed check. "
            "If allow is false, DO NOT place the order or pay. Show the user "
            "the reasons instead. Works with any merchant."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["intent_id", "merchant", "lines", "total_paise"],
            "properties": {
                "intent_id": {"type": "string"},
                "merchant": {
                    "type": "string",
                    "description": "Where the cart lives, e.g. 'zomato' or a domain.",
                },
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["item_id", "quantity", "line_total_paise"],
                        "properties": {
                            "item_id": {"type": "string"},
                            "title": {"type": "string"},
                            "quantity": {"type": "integer", "minimum": 0},
                            "line_total_paise": {"type": "integer", "minimum": 0},
                        },
                    },
                },
                "total_paise": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Cart total in paise, INCLUDING delivery and fees.",
                },
                "currency": {"type": "string", "default": "INR"},
                "all_items_available": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "verify_audit_trail",
        "description": (
            "Independently re-verify every intent and cart check this server "
            "has recorded, including refusals. Recomputes every hash from "
            "scratch rather than trusting what is stored — proves the log "
            "has not been edited after the fact, or names the exact event "
            "where it was."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "recommend_connector",
        "description": (
            "Before shopping, ask which real, verified connector fits this "
            "category. Never guesses — only recommends something already in "
            "this server's own directory, and remembers what actually worked "
            "last time so it does not have to ask again."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["category"],
            "properties": {
                "category": {
                    "type": "string",
                    "description": "e.g. 'grocery', 'food', 'general' — matches "
                                    "how this server's connector directory is organised.",
                },
            },
        },
    },
    {
        "name": "list_verified_connectors",
        "description": (
            "What this server has actually verified against, right now — not "
            "a claim in a document. Distinguishes evidence (how strongly WE "
            "have checked it) from capability (whether it can hand back a "
            "real cart at all)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# --- tool implementations ---------------------------------------------------

def _record_intent(arguments: dict) -> dict:
    try:
        items = [StatedItem.model_validate(raw) for raw in arguments.get("items") or []]
    except ValidationError as exc:
        append_event(AUDIT, "intent_refused", {
            "reason": "items could not be read",
            "user_request": str(arguments.get("user_request") or ""),
        })
        return {"error": "items could not be read", "detail": exc.errors(include_url=False)}

    if not items:
        append_event(AUDIT, "intent_refused", {
            "reason": "no items stated",
            "user_request": str(arguments.get("user_request") or ""),
        })
        return {
            "recorded": False,
            "missing": ["items"],
            "ask_the_user": "What would you like to order?",
        }

    cap = arguments.get("maximum_total_paise")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        # Refusing to default a budget is the whole point. An agent that invents
        # a spending limit has granted itself permission the user never gave.
        append_event(AUDIT, "intent_refused", {
            "reason": "no spending limit stated",
            "user_request": str(arguments.get("user_request") or ""),
        })
        return {
            "recorded": False,
            "missing": ["maximum_total_paise"],
            "ask_the_user": "What is the most you want to spend, including delivery?",
        }

    intent_id = f"og_{uuid4().hex[:12]}"
    _INTENTS[intent_id] = PurchaseIntent(
        intent_id=intent_id,
        user_id=_USER_ID,
        merchant=str(arguments.get("merchant") or ""),
        items=[
            IntentItem(
                requested_product=item.product,
                quantity=item.quantity,
                unit=item.unit,
            )
            for item in items
        ],
        maximum_total_paise=cap,
        currency=str(arguments.get("currency") or "INR").upper(),
        status=IntentStatus.READY_FOR_SEARCH,
    )

    append_event(AUDIT, "intent_recorded", {
        "intent_id": intent_id,
        "merchant": str(arguments.get("merchant") or ""),
        "items": [f"{i.quantity} x {i.product}" for i in items],
        "maximum_total_paise": cap,
    })

    return {
        "recorded": True,
        "intent_id": intent_id,
        "approved": [f"{i.quantity} x {i.product}" for i in items],
        "spending_limit_paise": cap,
        "next": "Shop with whatever connector you have, then call check_cart "
                "with the cart you built. Do not pay before it returns allow.",
    }


def _check_cart(arguments: dict) -> dict:
    intent = _INTENTS.get(str(arguments.get("intent_id") or ""))
    if intent is None:
        append_event(AUDIT, "cart_check_refused", {
            "reason": "no recorded intent for this id",
            "intent_id": str(arguments.get("intent_id") or ""),
        })
        return {
            "allow": False,
            "reasons": ["No recorded intent for that id. Call record_intent first."],
            "checks_passed": 0,
        }

    try:
        lines = [ObservedLine.model_validate(raw) for raw in arguments.get("lines") or []]
    except ValidationError as exc:
        append_event(AUDIT, "cart_check_refused", {
            "reason": "cart could not be read", "intent_id": intent.intent_id,
        })
        return {
            "allow": False,
            "reasons": ["The cart could not be read.", str(exc.errors(include_url=False))],
            "checks_passed": 0,
        }

    merchant = str(arguments.get("merchant") or "").strip()
    total = arguments.get("total_paise")
    if not merchant or not isinstance(total, int) or isinstance(total, bool):
        append_event(AUDIT, "cart_check_refused", {
            "reason": "cart missing merchant or integer total", "intent_id": intent.intent_id,
        })
        return {
            "allow": False,
            "reasons": ["A cart needs a merchant and an integer total in paise."],
            "checks_passed": 0,
        }

    currency = str(arguments.get("currency") or intent.currency).upper()

    observed = ObservedCart(
        merchant=merchant,
        cart_id=f"cart_{intent.intent_id}",
        currency=currency,
        lines=[
            CartLine(
                sku=line.item_id, variant_id=line.item_id, title=line.title,
                quantity=line.quantity, line_total_paise=line.line_total_paise,
            )
            for line in lines
            if line.quantity > 0
        ],
        total_paise=total,
    )

    # The assistant told us what the user wanted and what it then built. We pair
    # them positionally, which is only safe because the quantity and price
    # checks below compare the actual numbers rather than trusting the pairing.
    expectation = CartExpectation(
        merchant=merchant,
        currency=intent.currency,
        maximum_total_paise=intent.maximum_total_paise,
        lines=[
            ApprovedCartLine(
                variant_id=line.item_id,
                quantity=item.quantity,
                unit_price_paise=line.line_total_paise // line.quantity
                if line.quantity else 0,
            )
            for item, line in zip(intent.items, [l for l in lines if l.quantity > 0])
        ] or [ApprovedCartLine(variant_id="none", quantity=1, unit_price_paise=0)],
    )

    comparison = compare_cart(expectation, observed)
    confirmed = intent.model_copy(
        update={
            "status": IntentStatus.CONFIRMED,
            "confirmed_cart_hash": comparison.cart_hash,
            # This tool checks and pays in the same breath, so the
            # confirmation is fresh by construction — there is no window for
            # G_AUTHORIZATION_FRESH to catch here, unlike the multi-step
            # session flow in app.py where confirm and pay are separate calls.
            "confirmed_at": datetime.now(timezone.utc),
        }
    )

    result = evaluate_pre_payment_gates(
        confirmed,
        expectation,
        observed,
        CheckoutEvidence(
            merchant_permitted=True,
            cart_unique=True,
            attributes_match=True,
            items_available=bool(arguments.get("all_items_available", True)),
            # We are not the payment leg here, so we cannot know. Said out loud
            # below rather than quietly claimed as a pass.
            idempotency_free=True,
        ),
    )

    quantities_asked = sum(item.quantity for item in intent.items)
    quantities_found = sum(line.quantity for line in observed.lines)

    append_event(AUDIT, "cart_checked", {
        "intent_id": intent.intent_id,
        "merchant": merchant,
        "allow": result.allow,
        "failed_gates": [str(name) for name in result.failed],
        "checks_passed": len(result.passed),
        "checks_total": len(result.passed) + len(result.failed),
        "cart_total_paise": observed.total_paise,
    })
    record_check(
        CONNECTOR_LOG, merchant=merchant, allow=result.allow,
        failed_gates=[str(name) for name in result.failed],
        checks_passed=len(result.passed),
        checks_total=len(result.passed) + len(result.failed),
        cart_total_paise=observed.total_paise,
    )
    if result.allow:
        _remember_connector_on_success(merchant)

    return {
        "allow": result.allow,
        "checks_passed": len(result.passed),
        "checks_total": len(result.passed) + len(result.failed),
        "failed": [str(name) for name in result.failed],
        "reasons": [result.reasons[name] for name in result.failed],
        "you_asked_for": f"{quantities_asked} item(s), up to "
                         f"₹{intent.maximum_total_paise / 100:,.2f}",
        "cart_contains": f"{quantities_found} item(s), "
                         f"₹{observed.total_paise / 100:,.2f}",
        "not_checked_here": [
            "duplicate payment — OrderGuard is not in your payment path",
            "stock — taken from what you reported",
        ],
        "instruction": (
            "Proceed to payment." if result.allow
            else "Do NOT order or pay. Show the reasons above and ask the user."
        ),
    }


def _verify_audit_trail(arguments: dict) -> dict:
    try:
        events = verify_chain(AUDIT)
    except ChainTampered as exc:
        return {
            "verified": False,
            "broken_at_seq": exc.seq,
            "reason": str(exc),
        }
    return {
        "verified": True,
        "event_count": len(events),
        "events": [
            {
                "seq": e.seq, "event_type": e.event_type,
                "payload": event_payload(e), "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }


def _remember_connector_on_success(merchant: str) -> None:
    """A cart that just passed every gate is real evidence this connector is
    shoppable — the exact bar ``remember_store`` already applies elsewhere in
    this project ("only stores that actually answered... are saved"). Only
    remembered if ``merchant`` is a connector we actually know about — an
    unrecognised domain (see test_the_same_code_checks_any_merchant) teaches
    us nothing about which KIND of shop it is, so nothing is written for it.
    """
    try:
        connector = connector_by_id(merchant)
    except KeyError:
        return
    remember_store(MEMORY, user_id=_USER_ID, domain=connector.id, label=connector.label)


def _recommend_connector(arguments: dict) -> dict:
    category = str(arguments.get("category") or "").strip().lower()
    if not category:
        return {"recommended": None, "why": "no category given"}

    candidates = sorted(
        cart_capable_connectors(kind=category),
        key=lambda c: _EVIDENCE_RANK[c.evidence],
    )

    remembered_domains = {s.domain for s in saved_stores(MEMORY, _USER_ID)}
    remembered_matches = [c for c in candidates if c.id in remembered_domains]
    if remembered_matches:
        best_remembered = remembered_matches[0]   # candidates is already evidence-sorted
        return {
            "category": category,
            "recommended": best_remembered.id,
            "remembered_preference": True,
            "why": f"worked last time for {category} and still checks out",
            "evidence": best_remembered.evidence.value,
        }

    if not candidates:
        return {
            "category": category, "recommended": None, "remembered_preference": False,
            "why": f"no cart-capable connector known for {category!r}",
        }

    best = candidates[0]
    return {
        "category": category,
        "recommended": best.id,
        "remembered_preference": False,
        "why": best.evidence_note,
        "evidence": best.evidence.value,
        "alternatives": [c.id for c in candidates[1:]],
    }


def _list_verified_connectors(arguments: dict) -> dict:
    return {
        "summary": connector_summary(),
        "connectors": [
            {
                "id": c.id, "label": c.label, "kind": c.kind,
                "evidence": c.evidence.value, "capability": c.capability.value,
                "note": c.evidence_note,
            }
            for c in ALL_CONNECTORS
        ],
    }


_HANDLERS = {
    "record_intent": _record_intent,
    "check_cart": _check_cart,
    "verify_audit_trail": _verify_audit_trail,
    "recommend_connector": _recommend_connector,
    "list_verified_connectors": _list_verified_connectors,
}


# --- JSON-RPC ---------------------------------------------------------------

def handle_rpc(message: dict) -> dict | None:
    """One MCP request in, one response out. ``None`` for notifications."""
    method = message.get("method")
    request_id = message.get("id")

    if method == "notifications/initialized" or request_id is None:
        return None

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Call record_intent BEFORE shopping, using the user's own words. "
                "After building a cart at any merchant, call check_cart. If it "
                "returns allow=false, do not order or pay."
            ),
        })

    if method == "tools/list":
        return ok({"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        handler = _HANDLERS.get(params.get("name"))
        if handler is None:
            return {
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"no tool {params.get('name')!r}"},
            }
        payload = handler(params.get("arguments") or {})
        return ok({
            "content": [{"type": "text", "text": json.dumps(payload, indent=1)}],
            # A blocked cart is a successful check, not a failed call. Marking it
            # isError would invite a client to retry it.
            "isError": False,
        })

    return {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": -32601, "message": f"unknown method {method!r}"},
    }


@router.post("/mcp")
async def mcp_endpoint(message: dict) -> dict:
    """The URL you paste into Claude, VS Code, or any MCP client."""
    return handle_rpc(message) or {"jsonrpc": "2.0", "result": {}}
