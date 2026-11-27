"""Baseline vs mitigation: applying a mitigation and comparing the two versions."""

from decimal import Decimal as D

from pathguard_engine import Mitigation, ScenarioNode, Transition, apply_mitigation, compare
from pathguard_engine.examples import checklist_mitigation, checkout_launch
from pathguard_engine.ids import stable_id
from pathguard_engine.model import EstimateChange
from pathguard_engine.probability import Estimate

KEY = "example:checkout-launch"


def n(name: str) -> str:
    return stable_id("node", KEY, name)


def t(a: str, b: str) -> str:
    return stable_id("transition", KEY, n(a), n(b))


def test_checklist_variant_gives_the_exact_delta_and_keeps_the_baseline():
    plan, base = checkout_launch()
    before = base.model_copy(deep=True)
    vplan, variant, issues = apply_mitigation(plan, base, checklist_mitigation())
    assert issues == []
    assert base == before  # the baseline object is untouched

    c = compare((plan, base), (vplan, variant))
    assert c.baseline.first_pass.value == D("0.72675")
    assert c.variant.first_pass.value == D("0.81225")
    assert c.first_pass_delta.comparable and c.first_pass_delta.pp == D("8.55")
    assert c.eventual_delta.comparable and c.eventual_delta.pp > 0
    assert {(x.before, x.after) for x in c.changed_branches} == {
        ("85% (hypothetical)", "95% (hypothetical)"), ("15% (hypothetical)", "5% (hypothetical)")}
    assert c.variant.mitigation_cost == D("1200") and c.baseline.mitigation_cost is None
    assert "Requirements checklist" in c.variant.mitigations
    assert any("hypothetical" in f.lower() for f in c.flags)
    assert not any(f.startswith("Different coverage") for f in c.flags)


def test_changing_one_branch_requires_explicit_sibling_change():
    plan, base = checkout_launch()
    only_one = checklist_mitigation().model_copy(update={"estimate_changes": [
        EstimateChange(transition_id=t("scope", "build"),
                       new_probability=Estimate.hypothetical("95", "checklist"))]})
    _, _, issues = apply_mitigation(plan, base, only_one)
    msg = next(i.message for i in issues if i.code == "decision_invalid")
    assert "more than 100%" in msg and "Scope is ambiguous" in msg


def test_change_to_a_missing_link_is_rejected():
    plan, base = checkout_launch()
    bad = checklist_mitigation().model_copy(update={"estimate_changes": [
        EstimateChange(transition_id="trn_missing0001",
                       new_probability=Estimate.hypothetical("50", "x"))]})
    _, _, issues = apply_mitigation(plan, base, bad)
    assert any(i.code == "dangling_reference" for i in issues)


def test_mitigation_can_change_who_owns_a_task():
    plan, base = checkout_launch()
    task = plan.tasks[0]
    m = Mitigation(id="mit_owner00001", title="Name a scope owner", target_kind="task",
                   target_id=task.id, rationale="One owner", owner="Priya",
                   expected_effect="Faster scope decisions")
    vplan, _, issues = apply_mitigation(plan, base, m)
    assert issues == [] and vplan.tasks[0].owner == "Priya" and plan.tasks[0].owner is None


def test_added_safeguard_branch_is_flagged_as_different_coverage():
    plan, base = checkout_launch()
    guard = ScenarioNode(id="nod_guard00001", kind="recovery", label="Feature flag off")
    # Split the rollback decision: 30% now switch the flag off and retry QA instead.
    group = next(g for g in base.decision_groups if g.node_id == n("rollback"))
    m = Mitigation(
        id="mit_flag000001", title="Feature flag", target_kind="node", target_id=n("rollback"),
        rationale="Instant switch-off", expected_effect="Fewer failed rollbacks",
        estimate_changes=[
            EstimateChange(transition_id=t("rollback", "qa"),
                           new_probability=Estimate.hypothetical("60", "same")),
            EstimateChange(transition_id=t("rollback", "blocked"),
                           new_probability=Estimate.hypothetical("10", "flag helps"))])
    links = [
        Transition(id="trn_guard00001", source_node_id=n("rollback"), target_node_id=guard.id,
                   outcome="recovery", trigger="Switch the flag off", decision_group_id=group.id,
                   max_traversals=1, probability=Estimate.hypothetical("30", "flag helps")),
        Transition(id="trn_guard00002", source_node_id=guard.id, target_node_id=n("qa"),
                   outcome="recovery", trigger="Retry QA",
                   probability=Estimate.hypothetical("100", "flag works")),
    ]
    vplan, variant, issues = apply_mitigation(plan, base, m, add_nodes=[guard],
                                              add_transitions=links)
    assert issues == [], issues
    c = compare((plan, base), (vplan, variant))
    assert any(f.startswith("Different coverage") for f in c.flags)
    assert c.added_nodes == ["nod_guard00001"]
    assert {x.change for x in c.changed_branches} >= {"added", "changed"}
    assert c.eventual_delta.pp > 0


def test_partial_versions_are_not_compared():
    plan, base = checkout_launch()
    unknown = base.model_copy(update={"transitions": [
        x.model_copy(update={"probability": Estimate()}) if x.id == t("qa", "goal") else x
        for x in base.transitions]})
    c = compare((plan, base), (plan, unknown))
    assert not c.first_pass_delta.comparable and c.first_pass_delta.pp is None
    assert any("unresolved inputs" in f for f in c.flags)


def test_ranges_give_a_bounded_difference_only():
    plan, base = checkout_launch()

    def ranged(e: Estimate, lo: str, hi: str) -> Estimate:
        return e.model_copy(update={"kind": "range", "value": None, "low": D(lo), "high": D(hi)})

    variant = base.model_copy(update={"transitions": [
        x.model_copy(update={"probability": ranged(x.probability, "0.9", "0.98")})
        if x.id == t("scope", "build") else
        x.model_copy(update={"probability": ranged(x.probability, "0.02", "0.1")})
        if x.id == t("scope", "rework") else x for x in base.transitions]})
    d = compare((plan, base), (plan, variant)).first_pass_delta
    assert not d.comparable and d.pp is None and d.low_pp < d.high_pp
