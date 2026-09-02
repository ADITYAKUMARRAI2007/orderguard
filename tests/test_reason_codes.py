"""Every one of the 22 frozen gates has exactly one short code, and no two
gates share one — the two properties this module actually needs to hold.
"""

from orderguard.enums import GateName
from orderguard.reason_codes import EXTRA_CODES, REASON_CODES, code_for


def test_every_gate_has_a_code():
    assert set(REASON_CODES) == set(GateName)


def test_no_two_gates_share_a_code():
    assert len(set(REASON_CODES.values())) == len(REASON_CODES)


def test_every_code_follows_the_og_prefix_shape():
    for code in list(REASON_CODES.values()) + list(EXTRA_CODES.values()):
        assert code.startswith("OG-")
        assert code.count("-") == 2


def test_code_for_accepts_a_gatename_member():
    assert code_for(GateName.QUANTITIES_MATCH) == "OG-QTY-001"


def test_code_for_accepts_the_string_form_too():
    """diagnose() and GateResult both carry gate names as plain strings
    (str(GateName.X)) in places — code_for must work from either."""
    assert code_for(str(GateName.QUANTITIES_MATCH)) == "OG-QTY-001"
    assert code_for("G_QUANTITIES_MATCH") == "OG-QTY-001"


def test_code_for_extra_codes_by_name():
    assert code_for("INVALID_SIGNATURE") == EXTRA_CODES["INVALID_SIGNATURE"]
    assert code_for("PAYMENT_UNKNOWN") == EXTRA_CODES["PAYMENT_UNKNOWN"]


def test_code_for_an_unrecognised_name_degrades_to_empty_string_not_an_exception():
    assert code_for("something_nobody_defined") == ""
