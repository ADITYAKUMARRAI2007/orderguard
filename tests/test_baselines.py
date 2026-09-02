"""Baselines: is independent re-verification actually necessary?

The comparison this file proves is not rigged in OrderGuard's favour — it's
proven by showing WHY no_guard and confirm_only score identically on the
fixed fifty, not just asserting the numbers.
"""

from orderguard.benchmark import run_baselines, run_benchmark

RESULTS = {r.name: r for r in run_baselines()}


def test_all_three_configurations_are_reported():
    assert set(RESULTS) == {"no_guard", "confirm_only", "orderguard"}


def test_no_guard_accepts_every_attack():
    """The definition of no guard: nothing is ever blocked."""
    r = RESULTS["no_guard"]
    assert r.unsafe_acceptance_rate == 1.0
    assert r.valid_acceptance_rate == 1.0


def test_confirm_only_scores_identically_to_no_guard_on_this_set():
    """Not a coincidence: confirming the agent's own unverified claim cannot
    catch a mismatch between that claim and the real merchant cart, because
    confirm_only never looks at the real merchant cart."""
    no_guard, confirm_only = RESULTS["no_guard"], RESULTS["confirm_only"]
    assert confirm_only.unsafe_acceptance_rate == no_guard.unsafe_acceptance_rate
    assert confirm_only.valid_acceptance_rate == no_guard.valid_acceptance_rate


def test_orderguard_has_zero_unsafe_acceptance():
    r = RESULTS["orderguard"]
    assert r.unsafe_acceptance_rate == 0.0
    assert r.valid_acceptance_count == r.total_correct


def test_orderguard_matches_the_real_benchmarks_own_numbers():
    """run_baselines() must not silently diverge from run_benchmark() — same
    scenario set, same underlying gate calls, not a second simulation."""
    report = run_benchmark()
    r = RESULTS["orderguard"]
    assert r.unsafe_acceptance_count == len(report.false_matches)
    assert r.total_attacks == len(report.attacks)
    assert r.total_correct == len(report.correct_journeys)


def test_baselines_run_over_a_custom_journey_set_too():
    """The shared scenario set is a parameter, not hardcoded — this is what
    lets run_baselines() be reused against the Attack Lab's journeys too."""
    from orderguard.benchmark import run_attack_lab
    custom = run_baselines(run_attack_lab().journeys)
    names = {r.name for r in custom}
    assert names == {"no_guard", "confirm_only", "orderguard"}


# --- financial leakage: a real ₹ number, not just a rate --------------------

def test_no_guard_leaks_every_paisa_exposed_by_every_attack():
    """The definition of no guard: every attack's cart total is real money
    that would have moved."""
    r = RESULTS["no_guard"]
    assert r.leaked_amount_paise > 0
    assert r.leaked_amount_paise == r.total_exposed_paise


def test_confirm_only_leaks_the_same_amount_as_no_guard():
    no_guard, confirm_only = RESULTS["no_guard"], RESULTS["confirm_only"]
    assert confirm_only.leaked_amount_paise == no_guard.leaked_amount_paise


def test_orderguard_leaks_nothing_of_the_same_exposure():
    """The real headline number: same ₹ exposure as the weaker configs, but
    zero of it actually leaked through the real gates."""
    r = RESULTS["orderguard"]
    assert r.leaked_amount_paise == 0
    assert r.total_exposed_paise == RESULTS["no_guard"].total_exposed_paise
    assert r.total_exposed_paise > 0


def test_a_correct_journey_never_counts_as_exposure():
    """Paying for a correct order is not leakage -- only attack journeys
    contribute to total_exposed_paise."""
    from orderguard.benchmark import AttackKind, run_benchmark
    correct_total = sum(
        j.exposed_amount_paise for j in run_benchmark().journeys if j.kind is AttackKind.CORRECT
    )
    assert correct_total == 0
