"""The scorecard itself is a claim, and claims need tests.

Every property in DECISIONS.md D-031 is asserted here, so "zero false matches"
stays true after the next change rather than being a number written once and
left to rot. If someone weakens a gate later, this file is what catches it.
"""

from orderguard.benchmark import AttackKind, run_benchmark

REPORT = run_benchmark()


def test_the_benchmark_runs_exactly_fifty_journeys():
    assert REPORT.total == 50


def test_zero_false_matches():
    """The number a payments judge asks for first. No corrupted cart in this
    fixed set is allowed through, for any reason, including a model's own
    claim that it checked and the cart is fine."""
    assert REPORT.false_match_rate == 0.0
    assert REPORT.false_matches == []


def test_zero_false_blocks():
    """A gate that fires on a genuinely correct cart is not a safe failure —
    see F-011. Both directions are measured; only one is expected to be zero
    by definition of a WORKING guard, but both are worth watching."""
    assert REPORT.false_block_rate == 0.0


def test_every_genuinely_correct_cart_is_allowed():
    for journey in REPORT.correct_journeys:
        assert journey.allowed, journey.note


def test_wrong_quantity_is_always_blocked():
    """The founding example: bananas x60 against an approval of x6."""
    wrong_qty = [j for j in REPORT.journeys if j.kind is AttackKind.WRONG_QUANTITY]
    assert wrong_qty and all(not j.allowed for j in wrong_qty)
    assert all("G_QUANTITIES_MATCH" in j.failed_gates for j in wrong_qty)


def test_price_changed_under_the_cap_is_still_blocked():
    """F-010: the cap is a ceiling, not a price check. This is the exact case
    that was missed until a live store exposed it."""
    price_changed = [j for j in REPORT.journeys if j.kind is AttackKind.PRICE_CHANGED]
    assert price_changed and all(not j.allowed for j in price_changed)
    assert all("G_PRICES_MATCH" in j.failed_gates for j in price_changed)
    # and it really was under the cap — otherwise WITHIN_CAP would explain it
    assert all("G_WITHIN_CAP" not in j.failed_gates for j in price_changed)


def test_a_model_claiming_the_cart_is_fine_changes_nothing():
    claims = [j for j in REPORT.journeys if j.kind is AttackKind.MODEL_INSISTS_OK]
    assert claims and all(not j.allowed for j in claims)


def test_duplicate_checkout_never_produces_a_second_capture():
    assert REPORT.duplicate_business_effects == 0
    dup = [j for j in REPORT.journeys if j.kind is AttackKind.DUPLICATE_CHECKOUT]
    assert dup and all(j.allowed for j in dup)      # "allowed" here means "handled safely"


def test_a_cart_that_moved_after_confirmation_is_blocked():
    """D-004: the hash is frozen at confirmation. Anything different by
    payment time is a different purchase and needs fresh approval."""
    moved = [j for j in REPORT.journeys if j.kind is AttackKind.CART_CHANGED_AFTER_CONFIRM]
    assert moved and all(not j.allowed for j in moved)
    assert all("G_CONFIRMATION_MATCHES" in j.failed_gates for j in moved)


def test_every_attack_category_is_represented_and_fully_detected():
    """No category is allowed to hide inside a good overall average."""
    for kind, (correct, total) in REPORT.by_category.items():
        assert total > 0, kind
        if kind not in (AttackKind.CORRECT.value, AttackKind.DUPLICATE_CHECKOUT.value):
            assert correct == total, f"{kind}: {correct}/{total} blocked"


def test_the_markdown_report_states_the_headline_numbers():
    from orderguard.benchmark import render_markdown

    text = render_markdown(REPORT)
    assert "False-match rate" in text
    assert "0%" in text
    assert "Zero false matches" in text
