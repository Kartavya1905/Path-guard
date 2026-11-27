"""Risk register CSV, evidence gaps, estimate sources and outcome proposals."""

import csv
import io
from datetime import date
from decimal import Decimal as D

from pathguard_engine import ObservedOutcome, RiskCandidate, explore
from pathguard_engine.examples import checkout_launch, checkout_transaction
from pathguard_engine.ids import stable_id
from pathguard_engine.model import EstimateChange
from pathguard_engine.outcomes import apply_estimates, propose
from pathguard_engine.probability import Estimate
from pathguard_engine.report import estimate_sources, evidence_gaps, risk_register_csv

TX = "example:checkout-transaction"


def node(name: str) -> str:
    return stable_id("node", TX, name)


def link(a: str, b: str) -> str:
    return stable_id("transition", TX, node(a), node(b))


def test_csv_escapes_user_text_and_blocks_formulas():
    _, g = checkout_transaction()
    nasty = RiskCandidate(
        id="rsk_nasty00001",
        category="manual",
        title='Vendor "A", then B\nsecond line',
        trigger='=HYPERLINK("http://evil")',
        rationale="",
        consequence="+cmd|' /C calc'!A0",
        impact="high",
        review_status="accepted",
    )
    g = g.model_copy(update={"risks": [nasty]})
    text = risk_register_csv(g, explore(g), "Draft")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "Report status" and len(rows) == 2
    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["Risk"] == 'Vendor "A", then B\nsecond line'  # round-trips exactly
    assert row["Trigger"].startswith("'=")  # shown as text in spreadsheets
    assert row["Consequence"].startswith("'+")
    assert row["Report status"] == "Draft"


def test_evidence_gaps_and_estimate_sources():
    _, g = checkout_launch()
    gaps = evidence_gaps(g)
    kinds = {x.kind for x in gaps}
    assert {"risk_without_evidence", "estimate_without_evidence"} <= kinds
    sources = estimate_sources(g)
    assert all(s.source == "hypothetical" for s in sources)
    assert any(s.detail.startswith("Hypothetical: ") for s in sources)
    assert all(s.verification == "unverified" for s in sources)


def outcome(n: int, *, occ: int, exp: int, ctx=("web",), tid=None, on=date(2026, 11, 1)):
    return ObservedOutcome(
        id=f"out_{n:08d}",
        version_id="ver_00000001",
        decision_group_id=stable_id("decision_group", TX, node("pay")),
        transition_id=tid or link("pay", "confirm"),
        observed_on=on,
        exposure=exp,
        occurrences=occ,
        context=list(ctx),
        recorded_by="usr_demo0001",
    )


def test_proposal_uses_only_comparable_observations():
    _, g = checkout_transaction()
    obs = [
        outcome(1, occ=90, exp=100),
        outcome(2, occ=95, exp=100, on=date(2026, 11, 20)),
        outcome(3, occ=10, exp=100, ctx=("mobile",)),
        outcome(4, occ=8, exp=100, tid=link("pay", "declined")),
    ]
    p = propose(g, link("pay", "confirm"), obs, ["Web"])
    assert p.status == "candidate"
    assert p.candidate.value == D("0.925") and p.candidate.source.type == "measured"
    assert (p.occurrences, p.exposure) == (185, 200)
    assert (p.date_from, p.date_to) == (date(2026, 11, 1), date(2026, 11, 20))
    assert p.used == ["out_00000001", "out_00000002"]
    reasons = {e.outcome_id: e.reason for e in p.excluded}
    assert "Different context" in reasons["out_00000003"]
    assert "different branch" in reasons["out_00000004"]
    assert p.group_complete and [s.trigger for s in p.siblings] == ["Payment declined"]


def test_sparse_or_missing_data_stays_unresolved():
    _, g = checkout_transaction()
    assert propose(g, link("pay", "confirm"), [], ["web"]).status == "unresolved"
    sparse = propose(g, link("pay", "confirm"), [outcome(1, occ=9, exp=10)], ["web"])
    assert sparse.status == "unresolved" and "Too little data" in sparse.message
    assert sparse.candidate is None


def test_applying_one_branch_alone_names_the_sibling():
    _, g = checkout_transaction()
    change = EstimateChange(
        transition_id=link("pay", "confirm"),
        new_probability=Estimate.hypothetical("92.5", "observed"),
    )
    _, issues = apply_estimates(g, [change])
    assert issues and "Payment declined" in issues[0].message
    sibling = EstimateChange(
        transition_id=link("pay", "declined"),
        new_probability=Estimate.hypothetical("7.5", "observed"),
    )
    _, issues = apply_estimates(g, [change, sibling])
    assert issues == []
