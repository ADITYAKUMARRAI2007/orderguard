"""Diagnostics turn a gate failure into a diff, not just a sentence.

A judge asking "what exactly did the agent get wrong?" deserves an answer
shaped like this: what was approved, what showed up instead, which named gate
caught it. These tests prove the diff is genuinely built from the same data
the gates already computed — never a second opinion that could disagree with
the real decision.
"""

from datetime import datetime, timedelta, timezone

from orderguard.cart_verifier import ApprovedCartLine, CartExpectation, compare_cart
from orderguard.checkout_guard import CheckoutEvidence, evaluate_pre_payment_gates
from orderguard.diagnostics import diagnose
from orderguard.enums import GateName, IntentStatus
from orderguard.models import CartLine, IntentItem, ObservedCart, PurchaseIntent


def _expectation(**overrides) -> CartExpectation:
    data = dict(
        merchant="shop.example", maximum_total_paise=50000,
        lines=[ApprovedCartLine(variant_id="banana", quantity=6, unit_price_paise=1200)],
    )
    data.update(overrides)
    return CartExpectation(**data)


def _confirmed_intent(expectation: CartExpectation, observed: ObservedCart, **overrides) -> PurchaseIntent:
    comparison = compare_cart(expectation, observed)
    data = dict(
        intent_id="i1", user_id="u1", merchant=expectation.merchant,
        items=[IntentItem(requested_product="banana", quantity=6, unit="piece")],
        maximum_total_paise=expectation.maximum_total_paise,
        status=IntentStatus.CONFIRMED, confirmed_cart_hash=comparison.cart_hash,
        confirmed_at=datetime.now(timezone.utc),
    )
    data.update(overrides)
    return PurchaseIntent(**data)


def _correct_cart() -> ObservedCart:
    return ObservedCart(
        merchant="shop.example", cart_id="c1",
        lines=[CartLine(sku="banana", variant_id="banana", quantity=6, unit_price_paise=1200)],
        total_paise=7200,
    )


def _evidence(**overrides) -> CheckoutEvidence:
    data = dict(merchant_permitted=True, cart_unique=True, attributes_match=True,
                items_available=True, idempotency_free=True)
    data.update(overrides)
    return CheckoutEvidence(**data)


# --- a passing cart produces no diagnostics ---------------------------------

def test_a_correct_cart_produces_no_diagnostics():
    expectation = _expectation()
    observed = _correct_cart()
    intent = _confirmed_intent(expectation, observed)

    gates = evaluate_pre_payment_gates(intent, expectation, observed, _evidence())
    assert gates.allow

    diagnostics = diagnose(intent, expectation, observed, gates)
    assert diagnostics == []


# --- quantity mismatch -------------------------------------------------------

def test_a_quantity_mismatch_shows_expected_and_actual_counts():
    expectation = _expectation()
    correct = _correct_cart()
    intent = _confirmed_intent(expectation, correct)

    tampered = ObservedCart(
        merchant="shop.example", cart_id="c1",
        lines=[CartLine(sku="banana", variant_id="banana", quantity=60, unit_price_paise=1200)],
        total_paise=72000,
    )
    gates = evaluate_pre_payment_gates(intent, expectation, tampered, _evidence())
    assert not gates.allow

    diagnostics = {d.reason_code: d for d in diagnose(intent, expectation, tampered, gates)}
    qty = diagnostics[str(GateName.QUANTITIES_MATCH)]
    assert qty.decision == "BLOCK"
    assert qty.expected == {"banana": 6}
    assert qty.actual == {"banana": 60}


# --- price mismatch, expressed as line totals not unit prices ---------------

def test_a_price_mismatch_shows_quoted_versus_charged_totals():
    expectation = _expectation()
    correct = _correct_cart()
    intent = _confirmed_intent(expectation, correct)

    overcharged = ObservedCart(
        merchant="shop.example", cart_id="c1",
        lines=[CartLine(sku="banana", variant_id="banana", quantity=6, line_total_paise=48000)],
        total_paise=48000,
    )
    gates = evaluate_pre_payment_gates(intent, expectation, overcharged, _evidence())
    assert not gates.allow

    diagnostics = {d.reason_code: d for d in diagnose(intent, expectation, overcharged, gates)}
    price = diagnostics[str(GateName.PRICES_MATCH)]
    assert price.expected == {"banana": 7200}       # 6 x 1200 quoted
    assert price.actual == {"banana": 48000}         # charged instead


# --- merchant / currency / cap ----------------------------------------------

def test_a_wrong_merchant_names_both_shops():
    expectation = _expectation()
    correct = _correct_cart()
    intent = _confirmed_intent(expectation, correct)

    wrong_shop = ObservedCart(
        merchant="a-different-shop.example", cart_id="c1",
        lines=correct.lines, total_paise=correct.total_paise,
    )
    gates = evaluate_pre_payment_gates(intent, expectation, wrong_shop, _evidence())
    diagnostics = {d.reason_code: d for d in diagnose(intent, expectation, wrong_shop, gates)}

    merchant = diagnostics[str(GateName.MERCHANT_PERMITTED)]
    assert merchant.expected == "shop.example"
    assert merchant.actual == "a-different-shop.example"


def test_over_cap_shows_the_limit_and_the_actual_total():
    expectation = _expectation(maximum_total_paise=5000)
    correct = _correct_cart()
    intent = _confirmed_intent(expectation, correct, maximum_total_paise=5000)

    gates = evaluate_pre_payment_gates(intent, expectation, correct, _evidence())
    assert not gates.allow

    diagnostics = {d.reason_code: d for d in diagnose(intent, expectation, correct, gates)}
    cap = diagnostics[str(GateName.WITHIN_CAP)]
    assert cap.expected == {"maximum_total_paise": 5000}
    assert cap.actual == {"cart_total_paise": 7200}


# --- freshness ---------------------------------------------------------------

def test_a_stale_authorization_shows_its_age_against_the_limit():
    expectation = _expectation()
    correct = _correct_cart()
    an_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    intent = _confirmed_intent(expectation, correct, confirmed_at=an_hour_ago)

    gates = evaluate_pre_payment_gates(intent, expectation, correct, _evidence())
    assert not gates.allow

    diagnostics = {d.reason_code: d for d in diagnose(intent, expectation, correct, gates)}
    freshness = diagnostics[str(GateName.AUTHORIZATION_FRESH)]
    assert freshness.expected == {"max_age_seconds": 900}          # 15 minutes
    assert freshness.actual["age_seconds"] >= 3600


def test_an_unconfirmed_intent_is_diagnosed_as_never_confirmed():
    expectation = _expectation()
    correct = _correct_cart()
    intent = PurchaseIntent(
        intent_id="i1", user_id="u1", merchant="shop.example",
        items=[IntentItem(requested_product="banana", quantity=6, unit="piece")],
        maximum_total_paise=50000, status=IntentStatus.READY_FOR_CHECKOUT,
    )
    gates = evaluate_pre_payment_gates(intent, expectation, correct, _evidence())
    diagnostics = {d.reason_code: d for d in diagnose(intent, expectation, correct, gates)}
    assert diagnostics[str(GateName.AUTHORIZATION_FRESH)].actual == {"confirmed": False}


# --- diagnostics never override the real decision ---------------------------

def test_diagnostics_run_strictly_downstream_of_the_gate_result():
    """Passing an intentionally wrong GateResult must not make diagnose()
    invent failures the real gates never reported — it only explains what
    gates.failed already says, never recomputes whether something failed."""
    from orderguard.models import GateResult

    expectation = _expectation()
    correct = _correct_cart()
    intent = _confirmed_intent(expectation, correct)

    empty_result = GateResult(allow=True, passed=[], failed=[], reasons={})
    assert diagnose(intent, expectation, correct, empty_result) == []


def test_a_gate_with_no_natural_diff_is_silently_skipped():
    """MERCHANT_PERMITTED's allowlist check, ITEMS_AVAILABLE and
    IDEMPOTENCY_FREE are facts about external evidence, not a value
    comparison — there is nothing to diff, so diagnose() adds nothing for
    them rather than inventing a placeholder."""
    from orderguard.models import GateResult

    expectation = _expectation()
    correct = _correct_cart()
    intent = _confirmed_intent(expectation, correct)

    fake_failure = GateResult(
        allow=False, passed=[], failed=[GateName.ITEMS_AVAILABLE],
        reasons={str(GateName.ITEMS_AVAILABLE): "out of stock"},
    )
    assert diagnose(intent, expectation, correct, fake_failure) == []
