"""Graph validation: references, reachability, loops, groups and order conflicts."""

from pathguard_engine import (
    DecisionGroup,
    EvidenceLink,
    RiskCandidate,
    ScenarioGraph,
    ScenarioNode,
    Transition,
    readiness,
    validate_graph,
)
from tests.engine_factories import plan, prereq, tasks

PAD = "00000000"


def nid(name: str) -> str:
    return f"nod_{name}{PAD}"


def tid(a: str, b: str) -> str:
    return f"trn_{a}{b}{PAD}"


def node(name: str, kind: str = "step", **kw) -> ScenarioNode:
    terminal = kind in ("goal", "residual") or kw.pop("terminal", False)
    return ScenarioNode(id=nid(name), kind=kind, label=name, terminal=terminal, **kw)


def link(a: str, b: str, outcome: str = "success", **kw) -> Transition:
    return Transition(
        id=tid(a, b),
        source_node_id=nid(a),
        target_node_id=nid(b),
        outcome=outcome,
        trigger=f"{a} to {b}",
        **kw,
    )


def group(at: str, name: str = "g") -> DecisionGroup:
    return DecisionGroup(id=f"dgp_{name}{at}{PAD}", node_id=nid(at), label=f"At {at}")


def codes(g: ScenarioGraph, p=None) -> set[str]:
    return {i.code for i in validate_graph(g, p)}


def linear() -> ScenarioGraph:
    return ScenarioGraph(
        nodes=[node("s", "start"), node("a"), node("g", "goal")],
        transitions=[link("s", "a"), link("a", "g")],
    )


def test_a_simple_path_is_valid():
    assert codes(linear()) == set()


def test_dangling_references_are_reported():
    g = linear()
    g = g.model_copy(
        update={
            "transitions": [*g.transitions, link("a", "zz")],
            "risks": [
                RiskCandidate(
                    id="rsk_x0000000",
                    category="c",
                    title="t",
                    trigger="x",
                    rationale="r",
                    consequence="c",
                    affected_node_ids=[nid("gone")],
                )
            ],
            "evidence_links": [
                EvidenceLink(
                    id="evl_x0000000",
                    evidence_id="evd_missing0",
                    target_kind="node",
                    target_id=nid("a"),
                )
            ],
        }
    )
    issues = [i for i in validate_graph(g) if i.code == "dangling_reference"]
    assert {i.refs[1] for i in issues} == {nid("zz"), nid("gone"), "evd_missing0"}


def test_node_linked_to_missing_task():
    g = linear()
    g = g.model_copy(
        update={"nodes": [*g.nodes[:1], node("a", task_id="tsk_missing00"), g.nodes[2]]}
    )
    assert "dangling_reference" in codes(g, plan())


def test_duplicate_ids():
    g = linear()
    assert "duplicate_id" in codes(g.model_copy(update={"nodes": [*g.nodes, node("a")]}))


def test_missing_start_and_goal():
    assert {"start_missing", "goal_missing"} <= codes(
        ScenarioGraph(nodes=[node("a", terminal=True)])
    )


def test_unreachable_goal_and_nodes():
    g = ScenarioGraph(
        nodes=[node("s", "start"), node("a", terminal=True), node("b"), node("g", "goal")],
        transitions=[link("s", "a"), link("b", "g")],
    )
    issues = {i.code: i for i in validate_graph(g)}
    assert issues["goal_unreachable"].severity == "error"
    assert issues["node_unreachable"].severity == "warning"


def test_dead_end_and_terminal_with_exits():
    g = ScenarioGraph(
        nodes=[
            node("s", "start"),
            node("a"),
            node("f", "failure", terminal=True),
            node("g", "goal"),
        ],
        transitions=[link("s", "f"), link("f", "a")],
    )
    assert {"dead_end", "terminal_has_exits"} <= codes(g)


def test_unbounded_recovery_loop():
    nodes = [node("s", "start"), node("a"), node("f", "failure"), node("g", "goal")]
    grp, after = group("a"), group("f", "h")
    loop = [
        link("s", "a"),
        link("a", "g", decision_group_id=grp.id),
        link("a", "f", "failure", decision_group_id=grp.id),
        link("f", "a", "recovery", decision_group_id=after.id),
        link("f", "g", decision_group_id=after.id),
    ]
    g = ScenarioGraph(nodes=nodes, transitions=loop, decision_groups=[grp, after])
    assert codes(g) == {"unbounded_loop"}

    bounded = [
        t.model_copy(update={"max_traversals": 2}) if t.outcome == "recovery" else t for t in loop
    ]
    assert codes(g.model_copy(update={"transitions": bounded})) == set()


def test_loop_without_exit_and_limit_without_fallback():
    g = ScenarioGraph(
        nodes=[node("s", "start"), node("a"), node("b"), node("g", "goal")],
        transitions=[link("s", "a"), link("a", "b"), link("b", "a", "recovery", max_traversals=3)],
    )
    assert {"loop_without_exit", "retry_limit_without_fallback", "goal_unreachable"} <= codes(g)


def test_reconverging_paths_are_valid():
    grp = group("s")
    g = ScenarioGraph(
        nodes=[node("s", "start"), node("a"), node("b", "delay"), node("g", "goal")],
        transitions=[
            link("s", "a", decision_group_id=grp.id),
            link("s", "b", "delay", decision_group_id=grp.id),
            link("a", "g"),
            link("b", "g"),
        ],
        decision_groups=[grp],
    )
    assert codes(g) == set()


def test_branches_must_share_one_decision_group():
    g = ScenarioGraph(
        nodes=[node("s", "start"), node("a"), node("g", "goal")],
        transitions=[link("s", "a"), link("s", "g"), link("a", "g")],
    )
    assert codes(g) == {"ungrouped_branches"}


def test_group_source_mismatch_and_invalid_residual():
    grp = group("s")
    g = ScenarioGraph(
        nodes=[node("s", "start"), node("a"), node("g", "goal")],
        transitions=[link("s", "a"), link("a", "g", decision_group_id=grp.id)],
        decision_groups=[grp.model_copy(update={"residual_transition_id": tid("a", "g")})],
    )
    assert {"group_source_mismatch", "residual_invalid"} <= codes(g)


def test_suggested_and_rejected_items_are_not_modeled():
    g = linear()
    g = g.model_copy(
        update={
            "nodes": [
                *g.nodes,
                node("x", review_status="suggested"),
                node("y", review_status="rejected"),
            ],
            "transitions": [*g.transitions, link("a", "x", review_status="suggested")],
        }
    )
    assert codes(g) == set()


def test_map_order_must_respect_prerequisites():
    a, b = tasks("Design", "Build")
    p = plan([a, b], [prereq(a, b)])
    g = ScenarioGraph(
        nodes=[
            node("s", "start"),
            node("b", task_id=b.id),
            node("a", task_id=a.id),
            node("g", "goal"),
        ],
        transitions=[link("s", "b"), link("b", "a"), link("a", "g")],
    )
    assert codes(g, p) == {"map_order_conflict"}


def test_readiness_explains_what_blocks_analysis():
    r = readiness(plan(), ScenarioGraph())
    assert not r.can_analyze and not r.can_mark_reviewed
    assert r.blockers == ["No scenario map yet. Generate one from the plan first."]

    broken = ScenarioGraph(nodes=[node("s", "start")])
    r = readiness(plan(), broken)
    assert not r.can_analyze
    assert "must be fixed before the map can be analyzed" in r.blockers[0]
