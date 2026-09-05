"""What OrderGuard is allowed to remember, and what it may do with it.

Memory in a shopping agent is where safety quietly dies. A system that recalls
"they always buy the large pack" and acts on it has started spending on a guess.
So this module is written around four rules, each with a test.

**1. Only completed purchases become memory.**
A cart you abandoned is not a preference. An order that was refunded is not a
preference. ``remember_completed_order`` is the only writer of order history and
it demands a verified payment id, so there is no path from "the agent put
something in a cart" to "the agent believes you like it".

**2. Memory can never raise a spending cap.**
There is no function here that returns a budget, and nothing in this module
writes to ``maximum_total_paise``. That is not an oversight to be tidied up
later — it is the design. If a stored preference could lift a cap, then anything
that can write a preference can spend your money.

**3. What you say now beats what you said before.**
Memory only ever fills a gap you left silent. It never overrides a value present
in the current request.

**4. A suggestion is not an action.**
``suggest_reorder`` returns something to show you. Nothing here touches a cart.

Chat history is kept too, so a conversation survives a reload — but it is
stored as display text and is never fed back to a gate.

Same shared-SQLite-connection gap as ledger.py/capability.py (see
their docstrings): db.py::make_engine's one StaticPool connection for
":memory:"/file SQLite is unsafe for two real OS threads to call
commit() on at the same instant. Guarded the same way, for the same
reason.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, delete, select

from .db import make_engine


_MEMORY_LOCK = threading.Lock()

__all__ = [
    "ChatTurn",
    "RememberedOrder",
    "Preference",
    "SavedStore",
    "remember_store",
    "saved_stores",
    "forget_store",
    "memory_engine",
    "remember_chat_turn",
    "chat_history",
    "remember_completed_order",
    "recent_orders",
    "last_order",
    "suggest_reorder",
    "set_preference",
    "preferences",
    "forget_preference",
    "forget_everything",
    "apply_preferences_to_gaps",
]

_DEFAULT_PATH = Path("data/memory.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- tables ----------------------------------------------------------------

class ChatTurn(SQLModel, table=True):
    """One line of conversation, so a reload does not lose the thread.

    Display text only. No gate reads this table.
    """

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role: str                                   # "user" | "assistant"
    text: str
    created_at: datetime = Field(default_factory=_now)


class RememberedOrder(SQLModel, table=True):
    """A purchase that actually completed.

    ``payment_id`` is required. It is the evidence that this was a real,
    verified purchase rather than something an agent merely attempted.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    payment_id: str = Field(index=True)
    store: str
    store_label: str = ""
    variant_id: str
    title: str
    quantity: int
    unit_price_paise: int
    requested_as: str = ""                      # the words the user originally used
    created_at: datetime = Field(default_factory=_now)


class SavedStore(SQLModel, table=True):
    """A shop the user pointed us at that turned out to be shoppable.

    This is how the store list grows: not by us maintaining one, but by people
    naming shops. Only stores that actually answered with both search and cart
    tools are saved, so a saved store is a verified one.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    domain: str = Field(index=True)
    label: str = ""
    tools: str = ""                             # names only, comma separated
    created_at: datetime = Field(default_factory=_now)


class Preference(SQLModel, table=True):
    """Something the user asked us to remember, in their own words.

    ``scope`` is "always" or "session". A session preference is dropped when the
    session ends, which is what makes "just for today, get the small one" safe.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    key: str                                    # "unit", "brand", "store"
    value: str
    scope: str = "always"
    session_id: str = ""                        # set only when scope == "session"
    created_at: datetime = Field(default_factory=_now)


# --- engine ----------------------------------------------------------------

def memory_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    return make_engine(path)


# --- chat ------------------------------------------------------------------

def remember_chat_turn(
    engine: Engine, *, session_id: str, user_id: str, role: str, text: str
) -> None:
    if role not in {"user", "assistant"}:
        raise ValueError(f"role must be user or assistant, got {role!r}")
    with _MEMORY_LOCK, Session(engine) as db:
        db.add(ChatTurn(session_id=session_id, user_id=user_id, role=role, text=text))
        db.commit()


def chat_history(engine: Engine, session_id: str, limit: int = 200) -> list[ChatTurn]:
    with _MEMORY_LOCK, Session(engine) as db:
        return list(
            db.exec(
                select(ChatTurn)
                .where(ChatTurn.session_id == session_id)
                .order_by(ChatTurn.id)
                .limit(limit)
            )
        )


# --- order history ---------------------------------------------------------

def remember_completed_order(
    engine: Engine,
    *,
    user_id: str,
    payment_id: str,
    store: str,
    store_label: str,
    variant_id: str,
    title: str,
    quantity: int,
    unit_price_paise: int,
    requested_as: str = "",
) -> RememberedOrder:
    """The ONLY way an order enters memory.

    ``payment_id`` has no default on purpose. A caller that cannot name a
    verified payment cannot write history, so an abandoned or unpaid cart can
    never become a preference.
    """
    if not payment_id.strip():
        raise ValueError(
            "an order enters memory only with a verified payment id; "
            "an attempted purchase is not a purchase"
        )
    if quantity < 1:
        raise ValueError(f"a completed order needs a real quantity, got {quantity}")

    order = RememberedOrder(
        user_id=user_id, payment_id=payment_id, store=store, store_label=store_label,
        variant_id=variant_id, title=title, quantity=quantity,
        unit_price_paise=unit_price_paise, requested_as=requested_as,
    )
    with _MEMORY_LOCK, Session(engine) as db:
        db.add(order)
        db.commit()
        db.refresh(order)
    return order


def recent_orders(engine: Engine, user_id: str, limit: int = 10) -> list[RememberedOrder]:
    with _MEMORY_LOCK, Session(engine) as db:
        return list(
            db.exec(
                select(RememberedOrder)
                .where(RememberedOrder.user_id == user_id)
                .order_by(RememberedOrder.id.desc())
                .limit(limit)
            )
        )


def last_order(engine: Engine, user_id: str) -> RememberedOrder | None:
    orders = recent_orders(engine, user_id, limit=1)
    return orders[0] if orders else None


def suggest_reorder(engine: Engine, user_id: str) -> dict | None:
    """Describe the last purchase so the UI can offer it. Adds nothing.

    Deliberately returns plain data, not an Offer and not a cart line. Even the
    return type refuses to be something that could be dropped into a cart: the
    price is included so it can be *shown*, and it is re-checked against the
    live store like any other choice before anything is bought.
    """
    order = last_order(engine, user_id)
    if order is None:
        return None
    return {
        "title": order.title,
        "store": order.store,
        "store_label": order.store_label,
        "variant_id": order.variant_id,
        "quantity": order.quantity,
        "last_price_paise": order.unit_price_paise,
        "requested_as": order.requested_as,
        "bought_on": order.created_at.date().isoformat(),
        "note": "Last time's price. It will be checked against the store again.",
    }


# --- stores the user pointed us at ------------------------------------------

def remember_store(
    engine: Engine, *, user_id: str, domain: str, label: str = "",
    tools: tuple[str, ...] = (),
) -> SavedStore:
    """Save a shop that was verified shoppable. Saving twice is not an error."""
    with _MEMORY_LOCK, Session(engine) as db:
        existing = db.exec(
            select(SavedStore).where(
                SavedStore.user_id == user_id, SavedStore.domain == domain
            )
        ).first()
        if existing is not None:
            return existing

        store = SavedStore(
            user_id=user_id, domain=domain,
            label=label or domain.split(".")[0].title(),
            tools=",".join(tools),
        )
        db.add(store)
        db.commit()
        db.refresh(store)
        return store


def saved_stores(engine: Engine, user_id: str) -> list[SavedStore]:
    with _MEMORY_LOCK, Session(engine) as db:
        return list(
            db.exec(
                select(SavedStore)
                .where(SavedStore.user_id == user_id)
                .order_by(SavedStore.id)
            )
        )


def forget_store(engine: Engine, user_id: str, domain: str) -> int:
    with _MEMORY_LOCK, Session(engine) as db:
        result = db.exec(
            delete(SavedStore).where(
                SavedStore.user_id == user_id, SavedStore.domain == domain
            )
        )
        db.commit()
        return result.rowcount or 0


# --- preferences -----------------------------------------------------------

_ALLOWED_KEYS = frozenset({"unit", "brand", "store", "size", "diet"})


def set_preference(
    engine: Engine,
    *,
    user_id: str,
    key: str,
    value: str,
    scope: str = "always",
    session_id: str = "",
) -> Preference:
    """Store a preference. Note what is *not* in the allowed key list.

    There is no "budget", no "cap", no "auto_approve". The list is a closed set
    rather than free text, so a preference cannot grow into a permission.
    """
    if key not in _ALLOWED_KEYS:
        raise ValueError(
            f"{key!r} is not a preference OrderGuard will store. "
            f"Allowed: {sorted(_ALLOWED_KEYS)}. Spending limits and approvals "
            f"are never remembered — you state them each time."
        )
    if scope not in {"always", "session"}:
        raise ValueError(f"scope must be always or session, got {scope!r}")
    if scope == "session" and not session_id:
        raise ValueError("a session preference needs the session it belongs to")

    preference = Preference(
        user_id=user_id, key=key, value=value, scope=scope,
        session_id=session_id if scope == "session" else "",
    )
    with _MEMORY_LOCK, Session(engine) as db:
        db.add(preference)
        db.commit()
        db.refresh(preference)
    return preference


def preferences(engine: Engine, user_id: str, session_id: str = "") -> dict[str, str]:
    """Current preferences. Session ones only apply inside their own session."""
    with _MEMORY_LOCK, Session(engine) as db:
        rows = list(
            db.exec(
                select(Preference)
                .where(Preference.user_id == user_id)
                .order_by(Preference.id)
            )
        )
    resolved: dict[str, str] = {}
    for row in rows:
        if row.scope == "session" and row.session_id != session_id:
            continue
        resolved[row.key] = row.value        # a later one replaces an earlier one
    return resolved


def forget_preference(engine: Engine, user_id: str, key: str) -> int:
    with _MEMORY_LOCK, Session(engine) as db:
        result = db.exec(
            delete(Preference).where(
                Preference.user_id == user_id, Preference.key == key
            )
        )
        db.commit()
        return result.rowcount or 0


def forget_everything(engine: Engine, user_id: str) -> dict[str, int]:
    """Delete this user's memory. Offered plainly, because it has to be."""
    with _MEMORY_LOCK, Session(engine) as db:
        counts = {}
        for table in (Preference, RememberedOrder, ChatTurn, SavedStore):
            result = db.exec(delete(table).where(table.user_id == user_id))
            counts[table.__name__] = result.rowcount or 0
        db.commit()
        return counts


# --- using memory ----------------------------------------------------------

def apply_preferences_to_gaps(
    stated: dict[str, str], remembered: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Fill only the gaps. What the user said now always wins.

    Returns the merged values and a list of plain-English notes about which
    remembered values were used, so the UI can say so out loud. Memory that is
    applied silently is memory the user cannot correct.
    """
    merged = dict(stated)
    notes: list[str] = []
    for key, value in remembered.items():
        if stated.get(key):
            continue                     # rule 3: the current request wins
        merged[key] = value
        notes.append(f"Using your usual {key}: {value}.")
    return merged, notes
