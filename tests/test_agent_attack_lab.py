"""The agent-layer Attack Lab: every scenario calls the real function it
claims to attack, not a simulation. If any of these ever regress to
``correct == False``, that is a genuine security regression, not a flaky
test to retry.
"""

from orderguard.agent.attack_lab import run_agent_attack_lab


def test_every_agent_attack_scenario_is_correctly_handled():
    report = run_agent_attack_lab()
    incorrect = [r for r in report.results if not r.correct]
    assert incorrect == [], f"unsafe agent-layer scenarios: {[r.kind for r in incorrect]}"


def test_the_report_has_the_expected_scenario_count():
    report = run_agent_attack_lab()
    assert report.total == 14


def test_expanded_hostile_cases_are_real_benchmark_entries():
    kinds = {result.kind for result in run_agent_attack_lab().results}
    assert {
        "oauth_expiry_mid_mission",
        "connector_disconnect_mid_mission",
        "malicious_mcp_result_prompt_injection",
        "r2_external_commitment_without_approval",
        "duplicate_valid_webhook",
        "forged_webhook",
    } <= kinds


def test_every_scenario_has_a_real_explanatory_note():
    report = run_agent_attack_lab()
    for r in report.results:
        assert len(r.note) > 20
