"""Probability analysis: exact paths, ranges, unknowns, decisions, retries, limits."""

from decimal import Decimal as D

from pathguard_engine import ScenarioGraph, analyze
from pathguard_engine.examples import checkout_launch, checkout_transaction
from pathguard_engine.probability import Estimate
from tests.test_engine_graph import group, link, node


def est(kind: str, *vals: str) -> Estimate:
    if kind == "point":
        return Estimate.hypothetical(vals[0], "test")
    if kind == "range":
        e = Estimate.hypothetical("50", "test")
        return e.model_copy(update={"kind": "range", "value": None,
                                    "low": D(vals[0]) / 100, "high": D(vals[1]) / 100})
    return Estimate()


def gate(success: str, failure: str, *, complete: bool = True) -> ScenarioGraph:
    """start -> s, then a decision at s: success to the goal, failure to an end state."""
    g = group("s").model_copy(update={"complete": complete})
    return ScenarioGraph(
        nodes=[node("st", "start"), node("s"), node("g", "goal"),
               node("f", "failure", terminal=True)],
        transitions=[
            link("st", "s", probability=est("point", "100")),
            link("s", "g", probability=est(*success.split(":")), decision_group_id=g.id),
            link("s", "f", "failure", probability=est(*failure.split(":")),
                 decision_group_id=g.id),
        ],
        decision_groups=[g])


def gate_check(a):
    """The check for the decision at node "s" (groups are listed by node ID)."""
    return next(g for g in a.groups if g.group_id)


# --------------------------------------------------------------------------- the brief


def test_checkout_launch_first_pass_baseline_and_variant():
    base = analyze(checkout_launch()[1])
    variant = analyze(checkout_launch(variant=True)[1])
    assert base.first_pass.status == "exact" and base.first_pass.value == D("0.72675")
    assert variant.first_pass.value == D("0.81225")
    assert variant.first_pass.value - base.first_pass.value == D("0.0855")


def test_checkout_launch_eventual_success_includes_recovery():
    a = analyze(checkout_launch()[1])
    assert a.status == "exact"
    assert a.eventual_success.value > a.first_pass.value
    total = sum((v.value for v in a.outcomes.values()), D(0))
    assert total == 1  # mutually exclusive terminal outcomes, nothing double counted
    assert set(a.outcomes) == {"goal", "failure", "retries_exhausted"}
    assert any(p.uses_recovery and p.ends_in == "goal" for p in a.paths)


def test_checkout_transaction_primary_path():
    a = analyze(checkout_transaction()[1])
    assert a.first_pass.value == D("0.81144")
    assert a.eventual_success.value == D("0.81144")
    assert a.outcomes["failure"].value == 1 - D("0.81144")
    assert a.paths[0].ends_in == "goal" and a.paths[0].probability.value == D("0.81144")
    assert len(a.paths) == 4  # primary path plus the cart, payment and confirmation failures


# --------------------------------------------------------------------------- decisions


def test_complete_points_must_total_100():
    a = analyze(gate("point:85", "point:10"))
    assert a.status == "blocked"
    assert "add up to 95%" in gate_check(a).message


def test_over_100_is_invalid_even_when_incomplete():
    a = analyze(gate("point:85", "point:20", complete=False))
    assert a.status == "blocked" and "more than 100%" in a.issues[-1].message


def test_incomplete_decision_keeps_unmodeled_mass_visible():
    a = analyze(gate("point:85", "point:10", complete=False))
    assert a.status == "partial"
    assert a.outcomes["unmodeled"].low == D("0.05")
    assert a.eventual_success.low == D("0.85")  # guaranteed minimum, never normalized
    assert a.first_pass.status == "exact" and a.first_pass.value == D("0.85")


def test_unknown_estimate_gives_partial_not_a_guess():
    a = analyze(gate("point:85", "unknown"))
    assert a.status == "partial"
    assert a.eventual_success.low == D("0.85") and a.eventual_success.value is None
    assert a.missing_estimates
    assert gate_check(a).status == "unresolved"


def test_ranges_give_bounds_without_midpoints():
    a = analyze(gate("range:80:90", "range:10:20"))
    assert a.status == "bounds"
    assert (a.eventual_success.low, a.eventual_success.high) == (D("0.8"), D("0.9"))
    assert a.eventual_success.value is None


def test_infeasible_ranges_are_rejected():
    a = analyze(gate("range:50:60", "range:10:20"))
    assert a.status == "blocked" and "No combination" in gate_check(a).message


def test_non_exclusive_group_blocks_analysis():
    g = gate("point:85", "point:15")
    g = g.model_copy(update={"decision_groups": [
        g.decision_groups[0].model_copy(update={"exclusive": False})]})
    assert analyze(g).status == "blocked"


def test_structural_errors_block_analysis():
    a = analyze(ScenarioGraph(nodes=[node("st", "start")]))
    assert a.status == "blocked" and a.blockers


def test_single_link_needs_an_estimate():
    g = ScenarioGraph(nodes=[node("st", "start"), node("g", "goal")],
                      transitions=[link("st", "g")])
    a = analyze(g)
    assert a.status == "partial" and a.eventual_success.low == 0


# --------------------------------------------------------------------------- loops and limits


def retry_graph(limit: int | None = 1) -> ScenarioGraph:
    gs, gf = group("s"), group("f", "h")
    return ScenarioGraph(
        nodes=[node("st", "start"), node("s"), node("f", "failure"), node("g", "goal"),
               node("x", "failure", terminal=True)],
        transitions=[
            link("st", "s", probability=est("point", "100")),
            link("s", "g", probability=est("point", "85"), decision_group_id=gs.id),
            link("s", "f", "failure", probability=est("point", "15"), decision_group_id=gs.id),
            link("f", "s", "recovery", probability=est("point", "80"), decision_group_id=gf.id,
                 max_traversals=limit),
            link("f", "x", "failure", probability=est("point", "20"), decision_group_id=gf.id),
        ],
        decision_groups=[gs.model_copy(update={"complete": True}),
                         gf.model_copy(update={"complete": True})])


def test_bounded_retry_is_counted_per_run():
    a = analyze(retry_graph(limit=1))
    assert a.status == "exact"
    assert a.eventual_success.value == D("0.952")  # 0.85 + 0.15 * 0.8 * 0.85
    assert a.outcomes["retries_exhausted"].value == D("0.0144")
    assert a.outcomes["failure"].value == D("0.0336")
    assert a.first_pass.value == D("0.85")


def test_more_retries_raise_eventual_success():
    one = analyze(retry_graph(limit=1)).eventual_success.value
    three = analyze(retry_graph(limit=3)).eventual_success.value
    assert three > one


def test_depth_limit_truncates_and_is_reported():
    a = analyze(retry_graph(limit=50), max_depth=6)
    assert a.status == "partial" and a.limits.truncated
    assert "limit" in a.eventual_success.note


def test_reconverging_paths_are_not_double_counted():
    gs = group("s").model_copy(update={"complete": True})
    g = ScenarioGraph(
        nodes=[node("st", "start"), node("s"), node("a"), node("b", "delay"),
               node("g", "goal")],
        transitions=[
            link("st", "s", probability=est("point", "100")),
            link("s", "a", probability=est("point", "70"), decision_group_id=gs.id),
            link("s", "b", "delay", probability=est("point", "30"), decision_group_id=gs.id),
            link("a", "g", probability=est("point", "100")),
            link("b", "g", probability=est("point", "100")),
        ],
        decision_groups=[gs])
    a = analyze(g)
    assert a.eventual_success.value == 1
    assert a.first_pass.value == D("0.7")  # the delayed path is not clean first-pass


def test_result_count_limit_reports_omitted_paths():
    a = analyze(checkout_launch()[1], max_results=2)
    assert len(a.paths) == 2 and a.limits.paths_omitted == a.limits.paths_found - 2
