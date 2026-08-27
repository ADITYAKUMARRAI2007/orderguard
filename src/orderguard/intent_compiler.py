"""Turn a user request into either a complete intent or safe clarifications.

The model may propose a draft. It never controls the intent id, user id, state,
or whether the request is complete. Those are derived in deterministic code.
"""

from __future__ import annotations

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
    except (LLMUnavailable, ValidationError, ValueError) as exc:
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
        return CompilationResult(clarifications=clarifications)

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
        )
    )


def _missing_fields(draft: _DraftIntent) -> list[Clarification]:
    questions: list[Clarification] = []
    if not draft.merchant:
        questions.append(
            Clarification(
                reason=ClarificationReason.MISSING_MERCHANT,
                field="merchant",
                question="Which store would you like to shop from?",
            )
        )
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
