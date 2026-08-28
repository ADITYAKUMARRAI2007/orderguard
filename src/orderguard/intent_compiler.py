"""Turn a user request into either a complete intent or safe clarifications.

The model may propose a draft. It never controls the intent id, user id, state,
or whether the request is complete. Those are derived in deterministic code.
"""

from __future__ import annotations

import re

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .enums import ClarificationReason, IntentStatus
from .llm import LLMProvider, LLMUnavailable
from .models import Clarification, IntentItem, PurchaseIntent

__all__ = ["CompilationResult", "compile_intent"]

STRICT = ConfigDict(extra="forbid")


class _DraftItem(BaseModel):
    model_config = STRICT

    requested_product: str = Field(min_length=1)
    quantity: int | None = Field(default=None, ge=1)
    unit: str | None = None
    required_attributes: dict[str, str] = Field(default_factory=dict)
    preferred_attributes: dict[str, str] = Field(default_factory=dict)


class _DraftIntent(BaseModel):
    """The small, untrusted shape a provider is allowed to return."""

    model_config = STRICT

    merchant: str | None = None
    items: list[_DraftItem] = Field(default_factory=list)
    maximum_total_paise: int | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)


class CompilationResult(BaseModel):
    """Exactly one of complete intent or clarifications is present."""

    model_config = STRICT

    intent: PurchaseIntent | None = None
    clarifications: list[Clarification] = Field(default_factory=list)
    model_error: str = ""
    # The shop the user named, even when the request is still incomplete.
    # Without this the app could not check reachability until every other
    # question had been answered — so it asked a budget for a shop it was never
    # going to be able to use (F-015).
    draft_merchant: str = ""


def compile_intent(
    provider: LLMProvider,
    *,
    user_request: str,
    intent_id: str,
    user_id: str,
) -> CompilationResult:
    """Compile one request. Invalid or partial output never becomes an intent."""
    try:
        raw = provider.complete(
            system=(
                "Extract a shopping draft. Return only the requested schema. "
                "Use integer paise for a stated budget; omit unknown fields."
            ),
            user=user_request,
            schema=_DraftIntent.model_json_schema(),
        )
        draft = _DraftIntent.model_validate(raw)
    except LLMUnavailable as exc:
        # The service failed. Saying "I could not understand you" would blame
        # the user for our outage and invite them to rephrase a request that was
        # perfectly clear (F-017).
        return CompilationResult(
            clarifications=[
                Clarification(
                    reason=ClarificationReason.LOW_CONFIDENCE,
                    question=(
                        "I could not reach the service that reads your request, "
                        "so I have not done anything. Nothing was ordered. "
                        "Please try again in a moment."
                    ),
                )
            ],
            model_error=f"LLMUnavailable: {exc}",
        )
    except (ValidationError, ValueError) as exc:
        return CompilationResult(
            clarifications=[
                Clarification(
                    reason=ClarificationReason.LOW_CONFIDENCE,
                    question="I could not safely understand that order. What would you like to buy?",
                )
            ],
            model_error=type(exc).__name__,
        )

    clarifications = _missing_fields(draft)
    if clarifications:
        return CompilationResult(
            clarifications=clarifications, draft_merchant=draft.merchant or ""
        )

    return CompilationResult(
        intent=PurchaseIntent(
            intent_id=intent_id,
            user_id=user_id,
            merchant=draft.merchant or "",  # guarded by _missing_fields above
            items=[
                IntentItem(
                    requested_product=item.requested_product,
                    quantity=item.quantity or 0,  # guarded by _missing_fields above
                    unit=item.unit or "unit",
                    required_attributes=item.required_attributes,
                    preferred_attributes=item.preferred_attributes,
                )
                for item in draft.items
            ],
            maximum_total_paise=draft.maximum_total_paise or 0,
            currency=draft.currency.upper(),
            status=IntentStatus.READY_FOR_SEARCH,
        ),
        draft_merchant=draft.merchant or "",
    )


def label_answer(field: str, answer: str) -> str:
    """Attach a bare answer to the field it was asked about.

    The app used to append the raw reply — so "how many?" answered with "2"
    became a line reading just ``2``, which is ambiguous next to a request that
    already contains "under 400". The model kept asking the same question and
    the user was stuck in a loop (F-014).

    A number is also parsed here rather than left to the model. Code asked the
    question, so code should be able to read the answer.
    """
    text = (answer or "").strip()
    digits = re.sub(r"[^\d]", "", text)

    if field.endswith(".quantity"):
        index = re.search(r"\[(\d+)\]", field)
        which = f" for item {int(index.group(1)) + 1}" if index else ""
        if digits:
            return f"Quantity{which}: {int(digits)}"
        return f"Quantity{which}: {text}"

    if field == "maximum_total_paise":
        if digits:
            return f"Total budget including delivery: {int(digits)} rupees"
        return f"Total budget including delivery: {text}"

    if field == "merchant":
        return f"Shop to buy from: {text}"
    if field == "items":
        return f"Product wanted: {text}"

    return f"Additional detail: {text}"


def _missing_fields(draft: _DraftIntent) -> list[Clarification]:
    questions: list[Clarification] = []
    # The store is deliberately NOT asked for. Finding it is the product: we
    # search the allowed stores and the user decides by picking an offer. If the
    # shopper names one themselves, that is kept as a constraint on the draft
    # and enforced at selection.
    if not draft.items:
        questions.append(
            Clarification(
                reason=ClarificationReason.UNCLEAR_PRODUCT,
                field="items",
                question="What would you like to buy?",
            )
        )
    for index, item in enumerate(draft.items):
        if item.quantity is None:
            questions.append(
                Clarification(
                    reason=ClarificationReason.MISSING_QUANTITY,
                    field=f"items[{index}].quantity",
                    question=f"How many {item.requested_product} would you like?",
                )
            )
    # A zero cap is technically valid data but cannot support a purchase. Treat
    # it as an omitted budget until the user explicitly supplies a positive cap.
    if draft.maximum_total_paise is None or draft.maximum_total_paise == 0:
        questions.append(
            Clarification(
                reason=ClarificationReason.MISSING_BUDGET,
                field="maximum_total_paise",
                question="What is the most you would like to spend, including delivery?",
            )
        )
    return questions
