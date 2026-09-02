"""Every check_cart outcome, by merchant — the growing evidence log.

Deliberately simpler than the audit chain: no hash, no idempotency key, just
"which merchant, allowed or blocked, when" — cheap enough to query directly
rather than replaying the whole audit trail for one question.
"""

import pytest

from orderguard.connector_log import (
    checks_for_merchant, connector_log_engine, merchants_checked, record_check,
)


@pytest.fixture
def engine():
    return connector_log_engine(":memory:")


def test_a_passing_check_is_logged_with_no_failed_gates(engine):
    entry = record_check(
        engine, merchant="freshcart", allow=True, failed_gates=[],
        checks_passed=13, checks_total=13, cart_total_paise=16200,
    )
    assert entry.allow is True
    assert entry.failed_gates_csv == ""
    assert entry.cart_total_paise == 16200


def test_a_blocked_check_records_which_gates_failed(engine):
    entry = record_check(
        engine, merchant="zomato", allow=False,
        failed_gates=["G_QUANTITIES_MATCH", "G_WITHIN_CAP"],
        checks_passed=11, checks_total=13, cart_total_paise=338000,
    )
    assert entry.allow is False
    assert entry.failed_gates_csv == "G_QUANTITIES_MATCH,G_WITHIN_CAP"


def test_checks_for_a_merchant_accumulate_across_calls(engine):
    record_check(engine, merchant="zomato", allow=True, failed_gates=[],
                 checks_passed=13, checks_total=13, cart_total_paise=39000)
    record_check(engine, merchant="zomato", allow=False, failed_gates=["G_WITHIN_CAP"],
                 checks_passed=12, checks_total=13, cart_total_paise=338000)
    record_check(engine, merchant="freshcart", allow=True, failed_gates=[],
                 checks_passed=13, checks_total=13, cart_total_paise=16200)

    zomato_checks = checks_for_merchant(engine, "zomato")
    assert len(zomato_checks) == 2
    assert [c.allow for c in zomato_checks] == [True, False]


def test_merchants_checked_lists_distinct_merchants_in_first_seen_order(engine):
    record_check(engine, merchant="zomato", allow=True, failed_gates=[],
                 checks_passed=13, checks_total=13, cart_total_paise=39000)
    record_check(engine, merchant="freshcart", allow=True, failed_gates=[],
                 checks_passed=13, checks_total=13, cart_total_paise=16200)
    record_check(engine, merchant="zomato", allow=False, failed_gates=["G_WITHIN_CAP"],
                 checks_passed=12, checks_total=13, cart_total_paise=338000)

    assert merchants_checked(engine) == ["zomato", "freshcart"]


def test_a_merchant_never_checked_has_no_history(engine):
    assert checks_for_merchant(engine, "never-checked.example") == []
    assert merchants_checked(engine) == []
