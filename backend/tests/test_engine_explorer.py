"""Worst-case explorer: ranking, separate dimensions, cascades, evidence."""

from decimal import Decimal as D

from pathguard_engine import Evidence, EvidenceLink, RiskCandidate
from pathguard_engine.examples import checkout_transaction
from pathguard_engine.explorer import explore
from pathguard_engine.ids import stable_id
from pathguard_engine.probability import Estimate
from tests.test_engine_analysis import retry_graph
from tests.test_engine_graph import nid

TX = "example:checkout-transaction"


def tx_node(name: str) -> str:
    return stable_id("node", TX, name)


def risk(name: str, impact, affected=(), likelihood=None, status="accepted") -> RiskCandidate:
    return RiskCandidate(
        id=f"rsk_{name}00000000",
        category=name,
        title=name.title(),
        trigger="t",
        rationale="r",
        consequence="c",
        impact=impact,
        affected_node_ids=list(affected),
        likelihood=likelihood or Estimate(),
        review_status=status,
    )


def tx_with_risks():
    graph = checkout_transaction()[1]
    return graph.model_copy(
        update={
            "risks": [
                risk("declines", "high", [tx_node("pay"), tx_node("declined")]),
                risk("fraud", "critical"),  # unknown likelihood, no branch
                risk("typo", "low", likelihood=Estimate.hypothetical("50", "test")),
                risk("latency", "medium", [tx_node("cart")]),
                risk("ignored", "critical", status="rejected"),
            ]
        }
    )


def test_critical_unknown_risk_stays_on_top():
    ex = explore(tx_with_risks())
    assert [r.title for r in ex.risks] == ["Fraud", "Declines", "Latency", "Typo"]
    top = ex.risks[0]
    assert top.pinned_critical and top.likelihood.basis == "unknown"
    assert top.likelihood.worst_case == 1
    assert any("worst case" in s for s in top.reasons)
    assert any("always kept visible" in s for s in top.reasons)


def test_likelihood_impact_and_evidence_are_separate():
    declines = next(r for r in explore(tx_with_risks()).risks if r.title == "Declines")
    assert declines.impact == "high"
    assert declines.likelihood.basis == "modeled"
    assert declines.likelihood.value.value == D("0.092")  # 92% valid carts x 10% declines
    assert declines.evidence.strength == "none"
    typo = next(r for r in explore(tx_with_risks()).risks if r.title == "Typo")
    assert typo.likelihood.basis == "estimate" and typo.likelihood.value.value == D("0.5")


def test_cascade_is_traced_for_a_modeled_branch():
    declines = next(r for r in explore(tx_with_risks()).risks if r.title == "Declines")
    c = declines.cascade
    assert [s.label for s in c.steps] == ["Authorize payment", "Payment declined"]
    assert c.steps[1].via_outcome == "failure" and c.steps[1].probability.value == D("0.1")
    assert c.outcomes_if_it_happens["failure"].value == 1
    assert "end in failure: 100%" in c.summary


def test_cascade_through_a_bounded_retry():
    g = retry_graph(limit=1)
    g = g.model_copy(update={"risks": [risk("rework", "high", [nid("s"), nid("f")])]})
    r = explore(g).risks[0]
    assert r.likelihood.value.value == D("0.15")
    out = r.cascade.outcomes_if_it_happens
    assert (out["goal"].value, out["failure"].value, out["retries_exhausted"].value) == (
        D("0.68"),
        D("0.224"),
        D("0.096"),
    )
    assert r.cascade.ends_in == "failure"


def test_rejected_risks_are_excluded_and_counted():
    ex = explore(tx_with_risks())
    assert ex.total == 5 and ex.excluded_rejected == 1
    assert len(explore(tx_with_risks(), include_rejected=True).risks) == 5


def test_evidence_strength_is_the_best_linked_quality():
    g = tx_with_risks()
    target = next(r for r in g.risks if r.title == "Declines")
    g = g.model_copy(
        update={
            "evidence": [
                Evidence(
                    id="evd_strong0001", title="Gateway report", kind="dataset", quality="strong"
                ),
                Evidence(
                    id="evd_weak000001", title="Support chat", kind="interview", quality="weak"
                ),
            ],
            "evidence_links": [
                EvidenceLink(
                    id="evl_00000001",
                    evidence_id="evd_strong0001",
                    target_kind="risk",
                    target_id=target.id,
                ),
                EvidenceLink(
                    id="evl_00000002",
                    evidence_id="evd_weak000001",
                    target_kind="node",
                    target_id=tx_node("declined"),
                ),
            ],
        }
    )
    declines = next(r for r in explore(g).risks if r.title == "Declines")
    assert declines.evidence.strength == "strong" and declines.evidence.count == 2


def test_unanalyzable_map_falls_back_without_cascades():
    g = tx_with_risks()
    broken = g.model_copy(
        update={
            "decision_groups": [
                d.model_copy(update={"exclusive": False}) for d in g.decision_groups
            ]
        }
    )
    ex = explore(broken)
    assert not ex.analyzable
    declines = next(r for r in ex.risks if r.title == "Declines")
    assert declines.likelihood.basis == "unknown" and declines.cascade is None
