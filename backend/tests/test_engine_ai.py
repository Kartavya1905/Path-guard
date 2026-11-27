"""Optional AI suggestions: prompt scope, strict parsing, no numbers, review queue."""

import json

import pytest

from pathguard_engine import analyze, review_risk, validate_graph
from pathguard_engine.ai import (
    MAX_SUGGESTIONS,
    AiBatch,
    build_prompt,
    cache_key,
    parse,
    to_suggestions,
)
from pathguard_engine.examples import checkout_launch
from pathguard_engine.ids import stable_id

KEY = "example:checkout-launch"


def n(name: str) -> str:
    return stable_id("node", KEY, name)


def item(**kw) -> dict:
    base = {
        "type": "hazard",
        "title": "Tax rules differ by region",
        "affected_node_id": n("scope"),
        "trigger": "A new region is in scope",
        "consequence": "Wrong totals at checkout",
        "rationale": "Tax engines vary",
        "control": "Add tax cases to the checklist",
        "impact": "high",
    }
    return base | kw


def batch(*items: dict) -> AiBatch:
    return AiBatch.model_validate({"suggestions": list(items)})


def test_prompt_sends_only_nearby_nodes_and_no_numbers():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, n("qa"))
    payload = json.loads(p.messages[1]["content"])
    assert payload["start"] == plan.project.start and payload["focus"] == n("qa")
    assert n("qa") in p.node_ids and n("start") not in p.node_ids  # three hops away
    assert "probability" not in p.messages[1]["content"]
    assert "0.85" not in p.messages[1]["content"]  # estimates are never sent
    assert p.digest == build_prompt(plan, graph, n("qa")).digest  # deterministic
    assert len(build_prompt(plan, graph, None, max_chars=400).messages[1]["content"]) <= 400


def test_cache_key_depends_on_model_and_input():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, None)
    assert cache_key("ollama", "a", p) != cache_key("ollama", "b", p)
    assert cache_key("ollama", "a", p) == cache_key("ollama", "a", build_prompt(plan, graph, None))


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps({"suggestions": [item(probability=0.3)]}),  # numeric field not allowed
        json.dumps({"suggestions": [item(type="estimate")]}),
        json.dumps({"suggestions": [item()] * (MAX_SUGGESTIONS + 1)}),
        json.dumps({"ideas": []}),
    ],
)
def test_malformed_or_numeric_output_is_rejected(raw):
    with pytest.raises(ValueError):
        parse(raw)


def test_hazard_becomes_a_suggested_ai_risk_without_probability():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, None)
    conv = to_suggestions(batch(item()), graph, p.node_ids, "test-model")
    (risk,) = conv.risks
    assert risk.origin == "ai" and risk.review_status == "suggested"
    assert risk.likelihood.kind == "unknown" and risk.category == "ai_hazard"
    assert risk.mitigation_hint == "Add tax cases to the checklist"
    assert conv.rejected == []


def test_text_estimates_unknown_nodes_and_duplicates_are_rejected():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, None)
    conv = to_suggestions(
        batch(
            item(title="Fraud spike", consequence="About 30% of orders fail"),
            item(title="Ghost step", affected_node_id="nod_doesnotexist"),
            item(title="Unclear requirements"),  # already a seeded risk
            item(title="Payment API mismatch", affected_node_id=n("build")),
        ),
        graph,
        p.node_ids,
        "m",
    )
    assert conv.risks == []
    assert len(conv.rejected) == 4
    assert "numeric estimate" in conv.rejected[0] and "wasn't in the request" in conv.rejected[1]


def test_missing_step_points_back_to_the_plan():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, None)
    conv = to_suggestions(
        batch(item(type="step", title="Load test", control=None)), graph, p.node_ids, "m"
    )
    (risk,) = conv.risks
    assert (
        risk.title == "Missing step: Load test"
        and "Add “Load test” as a task" in risk.mitigation_hint
    )
    assert conv.nodes == []


def test_recovery_branch_can_be_accepted_and_analyzed():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, n("defect"))
    rec = item(
        type="recovery",
        title="Hotfix with vendor support",
        affected_node_id=n("defect"),
        rejoin_node_id=n("build"),
        trigger="Vendor confirms the fix",
    )
    conv = to_suggestions(batch(rec), graph, p.node_ids, "m")
    (risk,) = conv.risks
    assert len(conv.nodes) == 1 and len(conv.transitions) == 2
    assert all(t.probability.kind == "unknown" for t in conv.transitions)
    assert conv.transitions[0].max_traversals == 1  # bounded on the way in

    graph = graph.model_copy(
        update={
            "risks": [*graph.risks, *conv.risks],
            "nodes": [*graph.nodes, *conv.nodes],
            "transitions": [*graph.transitions, *conv.transitions],
        }
    )
    assert analyze(graph, plan).first_pass.value is not None  # suggestions aren't modeled
    graph = review_risk(graph, risk.id, "accept", model_branch=True)
    assert [i.code for i in validate_graph(graph, plan) if i.severity == "error"] == []
    a = analyze(graph, plan)
    assert a.status == "partial" and a.missing_estimates  # unknown until a person estimates


def test_recovery_needs_a_failure_step():
    plan, graph = checkout_launch()
    p = build_prompt(plan, graph, None)
    bad = item(type="recovery", affected_node_id=n("scope"), rejoin_node_id=n("build"))
    conv = to_suggestions(batch(bad), graph, p.node_ids, "m")
    assert conv.risks == [] and "recovery needs" in conv.rejected[0]
