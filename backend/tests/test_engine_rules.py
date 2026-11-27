"""Rule engine: generated map, suggestions, dedupe, regeneration and review."""

from datetime import date
from decimal import Decimal

import pytest

from pathguard_engine import (
    Plan,
    ProjectContext,
    ScenarioNode,
    build,
    generate,
    mark_edits,
    readiness,
    review_risk,
    validate_graph,
)
from pathguard_engine.rules import FAILED
from tests.engine_factories import plan, prereq, project, tasks

TODAY = date(2026, 11, 26)


def with_context(p: Plan, **ctx) -> Plan:
    return p.model_copy(
        update={"project": p.project.model_copy(update={"context": ProjectContext(**ctx)})}
    )


def checkout_plan() -> Plan:
    ts = tasks("Confirm requirements", "Build integration", "QA and staged rollout")
    return with_context(plan(ts, [prereq(ts[0], ts[1]), prereq(ts[1], ts[2])]), domain="checkout")


def errors(p: Plan, g) -> list[str]:
    return [i.code for i in validate_graph(g, p) if i.severity == "error"]


def spine_labels(g) -> list[str]:
    return [n.label for n in g.nodes if n.rule_key == "spine"]


def test_zero_task_plan_gives_a_valid_start_to_goal_map():
    p = plan()
    g = build(p, TODAY)
    assert [n.kind for n in g.nodes] == ["start", "goal"]
    assert len(g.transitions) == 1 and g.transitions[0].outcome == "success"
    assert g.risks == []
    r = readiness(p, g)
    assert r.can_analyze and r.can_mark_reviewed and r.issues == []


def test_spine_follows_dependency_order_not_display_order():
    a, b, c = tasks("A", "B", "C")
    p = plan([a, b, c], [prereq(c, a)])  # C must finish before A
    assert spine_labels(build(p)) == ["Checkout change approved", "B", "C", "A", "Reliable launch"]


def test_generation_is_deterministic():
    p = checkout_plan()
    assert build(p, TODAY) == build(p, TODAY)


def test_checkout_template_suggests_the_four_checkout_risks():
    p = checkout_plan()
    g = build(p, TODAY)
    titles = {t.id: t.title for t in p.tasks}
    found = {r.category: titles.get(r.affected_task_id or "") for r in g.risks}
    assert found == {
        "unclear_requirements": "Confirm requirements",
        "api_mismatch": "Build integration",
        "incomplete_testing": "QA and staged rollout",
        "release_defect": "QA and staged rollout",
    }
    for r in g.risks:
        assert r.review_status == "suggested" and r.origin == "rule"
        assert r.likelihood.kind == "unknown" and not r.likelihood.is_numeric
        assert r.title and r.trigger and r.rationale and r.consequence and r.mitigation_hint
        assert r.affected_node_ids
    for t in g.transitions:
        assert t.probability.kind == "unknown"


def test_suggested_branches_are_not_modeled_until_accepted():
    p = checkout_plan()
    g = build(p, TODAY)
    assert errors(p, g) == []
    r = readiness(p, g)
    assert r.can_analyze and not r.can_mark_reviewed
    assert r.pending_suggestions == 4
    assert "4 suggestion(s)" in r.blockers[0]


def test_accepting_a_branch_models_a_bounded_retry_and_a_terminal_failure():
    p = checkout_plan()
    g = build(p, TODAY)
    risk = next(r for r in g.risks if r.category == "release_defect")
    g = review_risk(g, risk.id, "accept", model_branch=True)
    branch = [t for t in g.transitions if t.rule_key == risk.dedupe_key]
    assert {t.outcome for t in branch} == {"failure", "recovery"}
    assert all(t.review_status == "accepted" for t in branch)
    retry = next(t for t in branch if t.outcome == "recovery")
    assert retry.max_traversals == 2
    failed = next(n for n in g.nodes if n.rule_key == FAILED)
    assert failed.review_status == "accepted" and failed.terminal
    assert errors(p, g) == []

    g = review_risk(g, risk.id, "reject")
    assert all(
        t.review_status == "rejected" for t in g.transitions if t.rule_key == risk.dedupe_key
    )
    assert next(n for n in g.nodes if n.rule_key == FAILED).review_status == "suggested"
    assert errors(p, g) == []


def test_accepting_without_modeling_keeps_the_branch_out_of_the_model():
    p = checkout_plan()
    g = build(p, TODAY)
    risk = g.risks[0]
    g = review_risk(g, risk.id, "accept")
    assert next(r for r in g.risks if r.id == risk.id).review_status == "accepted"
    assert all(
        t.review_status == "suggested" for t in g.transitions if t.rule_key == risk.dedupe_key
    )
    assert readiness(p, g).pending_suggestions == 3


def test_review_unknown_risk_raises():
    with pytest.raises(KeyError):
        review_risk(build(checkout_plan()), "rsk_doesnotexist", "accept")


def test_regenerating_adds_nothing_new_and_keeps_ids():
    p = checkout_plan()
    first = generate(p, None, TODAY)
    again = generate(p, first.graph, TODAY)
    assert again.added == [] and again.refreshed == [] and again.removed == []
    assert again.graph == first.graph


def test_regenerating_preserves_decisions_edits_and_manual_items():
    p = checkout_plan()
    g = generate(p, None, TODAY).graph
    by_cat = {r.category: r for r in g.risks}
    g = review_risk(g, by_cat["release_defect"].id, "accept", model_branch=True)
    g = review_risk(g, by_cat["api_mismatch"].id, "reject")
    edited = g.model_copy(
        update={
            "risks": [
                r.model_copy(update={"title": "Scope creep from marketing"})
                if r.id == by_cat["unclear_requirements"].id
                else r
                for r in g.risks
            ]
        }
    )
    g = mark_edits(g, edited)
    manual = ScenarioNode(id="nod_manual0001", kind="step", label="Manual step")
    g = g.model_copy(update={"nodes": [*g.nodes, manual]})

    out = generate(p, g, TODAY)
    risks = {r.category: r for r in out.graph.risks}
    assert risks["release_defect"].review_status == "accepted"
    assert risks["api_mismatch"].review_status == "rejected"
    assert risks["unclear_requirements"].review_status == "edited"
    assert risks["unclear_requirements"].title == "Scope creep from marketing"
    assert risks["incomplete_testing"].review_status == "suggested"
    assert any(n.id == manual.id for n in out.graph.nodes)
    assert out.added == [] and out.removed == []
    assert by_cat["release_defect"].id in out.preserved


def test_regenerating_after_plan_changes_updates_the_spine():
    ts = tasks("Design", "Build", "Ship")
    p = plan(ts, [prereq(ts[0], ts[1]), prereq(ts[1], ts[2])])
    g = generate(p, None, TODAY).graph
    layout = next(n for n in g.nodes if n.label == "Design").layout
    moved = g.model_copy(
        update={
            "nodes": [
                n.model_copy(update={"layout": layout.model_copy(update={"y": 999})})
                if n.label == "Design"
                else n
                for n in g.nodes
            ]
        }
    )

    renamed = ts[0].model_copy(update={"title": "Design review"})
    p2 = p.model_copy(update={"tasks": [renamed, ts[2]], "prerequisites": [prereq(renamed, ts[2])]})
    out = generate(p2, moved, TODAY)
    assert spine_labels(out.graph) == [
        "Checkout change approved",
        "Design review",
        "Ship",
        "Reliable launch",
    ]
    assert next(n for n in out.graph.nodes if n.label == "Design review").layout.y == 999
    assert out.removed  # the "Build" node and its links
    assert errors(p2, out.graph) == []


def test_suggestions_are_deduplicated():
    p = checkout_plan()
    keys = [r.dedupe_key for r in build(p, TODAY).risks]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- generic rules


def categories(p: Plan) -> list[str]:
    return sorted(r.category for r in build(p, TODAY).risks)


def test_no_generic_suggestions_without_enough_information():
    assert categories(plan(tasks("Only task"))) == []


def test_missing_prerequisites_and_conflicting_order():
    a, b = tasks("First", "Second")
    assert categories(plan([a, b])) == ["missing_prerequisite"]
    assert categories(plan([a, b], [prereq(b, a)])) == ["conflicting_order"]


def test_dependency_cycle_is_suggested_and_map_still_builds():
    a, b = tasks("A", "B")
    p = plan([a, b], [prereq(a, b), prereq(b, a)])
    g = build(p, TODAY)
    assert "conflicting_order" in categories(p)
    assert spine_labels(g) == ["Checkout change approved", "A", "B", "Reliable launch"]


def test_approval_bottleneck_adds_a_delay_branch_that_rejoins():
    a, b = tasks("Legal sign-off", "Launch")
    p = plan([a, b], [prereq(a, b)])
    g = build(p, TODAY)
    risk = next(r for r in g.risks if r.category == "approval_bottleneck")
    g = review_risk(g, risk.id, "accept", model_branch=True)
    rejoin = next(
        t for t in g.transitions if t.rule_key == risk.dedupe_key and t.outcome == "success"
    )
    launch = next(n for n in g.nodes if n.task_id == b.id)
    assert rejoin.target_node_id == launch.id
    assert errors(p, g) == []


def test_stakeholders_trigger_an_approval_bottleneck():
    p = with_context(plan(), stakeholders=["Finance", "Legal", "Ops"])
    assert categories(p) == ["approval_bottleneck"]


def test_handoff_and_resource_conflicts_use_owners():
    a, b, c = tasks("Build API", "Build UI", "Release")
    a = a.model_copy(update={"owner": "Ana"})
    b = b.model_copy(update={"owner": "Ana"})
    c = c.model_copy(update={"owner": "Ben"})
    p = plan([a, b, c], [prereq(a, c), prereq(b, c)])
    assert categories(p) == ["handoff_delay", "handoff_delay", "resource_conflict"]


def test_deadline_conflict_uses_the_longest_dependency_chain():
    a, b, c = tasks("A", "B", "C")
    a = a.model_copy(update={"duration_days": Decimal("5")})
    b = b.model_copy(update={"duration_days": Decimal("5")})
    c = c.model_copy(update={"duration_days": Decimal("5")})
    parallel = with_context(
        plan([a, b, c], [prereq(a, c), prereq(b, c)]), deadline=date(2026, 12, 6)
    )
    assert "deadline_conflict" not in categories(parallel)  # 10 days needed, 10 available
    chain = with_context(plan([a, b, c], [prereq(a, b), prereq(b, c)]), deadline=date(2026, 12, 6))
    assert "deadline_conflict" in categories(chain)  # 15 days needed
    late = with_context(plan(), deadline=date(2026, 1, 1))
    assert categories(late) == ["deadline_conflict"]


def test_budget_conflict():
    a, b = tasks("A", "B")
    a = a.model_copy(update={"cost": Decimal("600")})
    b = b.model_copy(update={"cost": Decimal("500")})
    p = Plan(
        project=project(context=ProjectContext(budget=Decimal("1000"), currency="USD")),
        tasks=[a, b],
        prerequisites=[prereq(a, b)],
    )
    risk = next(r for r in build(p, TODAY).risks if r.rule_id == "generic.budget_conflict")
    assert "1100 USD" in risk.trigger and "1000 USD" in risk.trigger
