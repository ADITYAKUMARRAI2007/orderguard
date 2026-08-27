"""Compare an approved cart against what a merchant actually reports.

This module deliberately has no network, model, checkout, or payment code. It
answers one narrow question: does the cart read back from the merchant still
match the exact variants, quantities, currency, and cap the user approved?
The payment gate will consume this result later; it must never be able to
replace it with a model opinion.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import ObservedCart

__all__ = [
    "ApprovedCartLine",
    "CartExpectation",
    "CartComparison",
    "cart_hash",
    "compare_cart",
]

STRICT = ConfigDict(extra="forbid", frozen=True)


class ApprovedCartLine(BaseModel):
    """A specific merchant variant, quantity, and quoted price chosen by the user.

    ``unit_price_paise`` is the price shown on screen at the moment of choosing.
    It has no default. A caller that has not carried the quoted price this far
    gets a validation error rather than an unchecked cart, because the failure
    mode of forgetting it is silent and expensive — see the note on
    ``matches_prices``.
    """

    model_config = STRICT

    variant_id: str = Field(min_length=1)
    quantity: int = Field(ge=1)
    unit_price_paise: int = Field(ge=0)


class CartExpectation(BaseModel):
    """The deterministic portion of what the user approved before checkout."""

    model_config = STRICT

    merchant: str = Field(min_length=1)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    maximum_total_paise: int = Field(ge=0)
    lines: list[ApprovedCartLine] = Field(min_length=1)

    @model_validator(mode="after")
    def does_not_repeat_a_variant(self) -> "CartExpectation":
        ids = [line.variant_id for line in self.lines]
        if len(ids) != len(set(ids)):
            raise ValueError("an approved cart may not contain the same variant twice")
        return self


class CartComparison(BaseModel):
    """Facts from an independent cart read. This is not a payment authorisation."""

    model_config = STRICT

    matches_merchant: bool
    matches_currency: bool
    matches_quantities: bool
    matches_prices: bool
    within_cap: bool
    cart_hash: str
    failures: list[str] = Field(default_factory=list)

    @property
    def matches(self) -> bool:
        return not self.failures


def cart_hash(cart: ObservedCart) -> str:
    """Hash stable, typed cart facts — never prose or a mutable checkout URL."""
    payload = {
        "merchant": cart.merchant.lower(),
        "currency": cart.currency.upper(),
        "total_paise": cart.total_paise,
        "lines": sorted(
            (
                {
                "variant_id": _line_id(line),
                "quantity": line.quantity,
                "line_total_paise": line.line_total_paise,
                }
                for line in cart.lines
            ),
            key=lambda line: line["variant_id"],
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def compare_cart(expected: CartExpectation, observed: ObservedCart) -> CartComparison:
    """Compare two typed cart states without fuzzy matching or model judgment."""
    failures: list[str] = []

    matches_merchant = observed.merchant.lower() == expected.merchant.lower()
    if not matches_merchant:
        failures.append(
            f"Merchant is {observed.merchant}; the approved merchant is {expected.merchant}."
        )

    matches_currency = observed.currency.upper() == expected.currency.upper()
    if not matches_currency:
        failures.append(
            f"Cart currency is {observed.currency}; the approved currency is {expected.currency}."
        )

    expected_quantities = {line.variant_id: line.quantity for line in expected.lines}
    observed_quantities: dict[str, int] = {}
    for line in observed.lines:
        identifier = _line_id(line)
        observed_quantities[identifier] = observed_quantities.get(identifier, 0) + line.quantity

    matches_quantities = observed_quantities == expected_quantities
    if not matches_quantities:
        failures.append(
            "Cart variants or quantities differ from the approved cart. "
            f"Expected {expected_quantities}; observed {observed_quantities}."
        )

    # The quoted price is approved separately from the cap. A cap is a ceiling:
    # it permits every price beneath it, including one the user never saw. If a
    # merchant quotes ₹12 during the search and charges ₹80 in the cart, six
    # bananas still cost less than a ₹500 cap and every identity check above
    # still passes. Only comparing against the quoted price catches that.
    expected_prices = {line.variant_id: line.unit_price_paise for line in expected.lines}
    observed_prices: dict[str, set[int]] = {}
    for line in observed.lines:
        observed_prices.setdefault(_line_id(line), set()).add(line.unit_price_paise)

    price_failures = []
    for variant_id, approved_price in expected_prices.items():
        seen = observed_prices.get(variant_id)
        if seen is None:
            continue                     # absence is already a quantity failure
        if seen != {approved_price}:
            price_failures.append(
                f"{variant_id} was quoted at {approved_price} paise each "
                f"but the cart charges {sorted(seen)}"
            )

    matches_prices = not price_failures
    if not matches_prices:
        failures.append("Cart prices differ from the quoted prices. " + "; ".join(price_failures))

    total = observed.total_paise
    within_cap = total is not None and total <= expected.maximum_total_paise
    if not within_cap:
        failures.append(
            f"Cart total is {total} paise; approved cap is {expected.maximum_total_paise} paise."
        )

    return CartComparison(
        matches_merchant=matches_merchant,
        matches_currency=matches_currency,
        matches_quantities=matches_quantities,
        matches_prices=matches_prices,
        within_cap=within_cap,
        cart_hash=cart_hash(observed),
        failures=failures,
    )


def _line_id(line) -> str:
    """External carts should expose a variant id; SKU is the safe demo-store fallback."""
    identifier = line.variant_id or line.sku
    if not identifier:
        raise ValueError("observed cart line has no stable variant or SKU id")
    return identifier
