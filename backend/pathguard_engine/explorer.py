"""Worst-case explorer: rank risks and trace their modeled cascades.

Likelihood, impact and evidence strength are reported separately and never
blended into one number. The order is:

1. impact (critical first; risks with no impact set come last),
2. worst-case likelihood: the upper bound of the modeled or estimated
   likelihood. An unknown likelihood counts as "up to 100%", so a critical
   risk with unknown likelihood stays at the top instead of disappearing,
3. title, for a stable order.

Likelihood comes, in order of preference, from the model (the chance a run
reaches the risk's accepted failure branch), from the risk's own estimate, or
is unknown. Nothing is guessed.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.analysis import (
    ONE,
    ZERO,
    Value,
    _prepared,
    _value,
    interval,
    outcomes_from,
    reach_probability,
)
from pathguard_engine.graph import modeled
from pathguard_engine.model import RiskCandidate, ScenarioGraph, Severity, Transition

IMPACT_TIER: dict[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}
EVIDENCE_RANK: dict[str, int] = {"strong": 4, "moderate": 3, "weak": 2, "anecdotal": 1}
Strength = Literal["strong", "moderate", "weak", "anecdotal", "none"]

ORDER_NOTE = ("Order: impact first (critical at the top), then worst-case likelihood (the upper "
              "bound; unknown counts as up to 100%), then title. Likelihood, impact and evidence "
              "are shown separately and never combined into one score.")


class Likelihood(BaseModel):
    basis: Literal["modeled", "estimate", "unknown"]
    value: Value | None = None
    worst_case: Decimal  # upper bound used for ordering; 1 when unknown
    note: str


class EvidenceSummary(BaseModel):
    strength: Strength
    count: int
    titles: list[str]


class CascadeStep(BaseModel):
    node_id: str
    label: str
    kind: str
    via_outcome: str | None = None
    via_trigger: str | None = None
    probability: Value | None = None  # conditional chance of the link taken to get here


class Cascade(BaseModel):
    steps: list[CascadeStep]
    ends_in: str
    outcomes_if_it_happens: dict[str, Value]
    summary: str


class RiskView(BaseModel):
    id: str
    title: str
    category: str
    trigger: str
    consequence: str
    review_status: str
    origin: str
    impact: Severity | None
    affected_node_ids: list[str]
    likelihood: Likelihood
    evidence: EvidenceSummary
    rank: int = 0
    reasons: list[str] = []
    pinned_critical: bool
    modeled_branch: bool
    cascade: Cascade | None = None


class Explorer(BaseModel):
    risks: list[RiskView]
    total: int
    excluded_rejected: int
    analyzable: bool
    categories: list[str]
    order_note: str = ORDER_NOTE


def _pct(d: Decimal) -> str:
    s = f"{d * 100:f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) + "%"


def _describe(v: Value) -> str:
    if v.status == "exact":
        return _pct(v.value)  # type: ignore[arg-type]
    if v.status == "bounds":
        return f"{_pct(v.low)}–{_pct(v.high)}"
    return "unknown" if v.low == 0 else f"at least {_pct(v.low)}"


def _branch_node(graph: ScenarioGraph, risk: RiskCandidate) -> str | None:
    """The risk's failure/delay node, if it is part of the modeled map."""
    nodes = {n.id: n for n in graph.nodes}
    for nid in risk.affected_node_ids[1:]:
        n = nodes.get(nid)
        if n is not None and modeled(n) and any(
                t.target_node_id == nid and modeled(t) for t in graph.transitions):
            return nid
    return None


def _likelihood(graph: ScenarioGraph, risk: RiskCandidate, branch: str | None,
                analyzable: bool) -> Likelihood:
    if branch and analyzable:
        v = reach_probability(graph, branch)
        if v is not None:
            note = f"Modeled: runs reach this branch {_describe(v)} of the time."
            if v.status == "partial":
                note += " Partial, because some estimates are missing or outcomes unmodeled."
            return Likelihood(basis="modeled", value=v, worst_case=ONE if v.status == "partial"
                              else v.high, note=note)
    e = risk.likelihood
    if e.kind != "unknown":
        lo, hi = (e.value, e.value) if e.kind == "point" else (e.low, e.high)
        v = _value(lo, hi, False)  # type: ignore[arg-type]
        return Likelihood(basis="estimate", value=v, worst_case=v.high,
                          note=f"Estimated at {_describe(v)} ({e.source.type}).")
    return Likelihood(basis="unknown", worst_case=ONE,
                      note="Likelihood unknown, so it is ranked by its worst case (up to 100%).")


def _evidence(graph: ScenarioGraph, risk: RiskCandidate) -> EvidenceSummary:
    targets = {("risk", risk.id)} | {("node", n) for n in risk.affected_node_ids}
    ids = {link.evidence_id for link in graph.evidence_links
           if (link.target_kind, link.target_id) in targets}
    items = [e for e in graph.evidence if e.id in ids]
    if not items:
        return EvidenceSummary(strength="none", count=0, titles=[])
    best = max(items, key=lambda e: EVIDENCE_RANK[e.quality]).quality
    return EvidenceSummary(strength=best, count=len(items), titles=[e.title for e in items])


def _cascade(graph: ScenarioGraph, risk: RiskCandidate, branch: str) -> Cascade | None:
    """The most likely modeled path from the risk to a bad end, with how runs end from there."""
    outcomes = outcomes_from(graph, branch)
    if outcomes is None:
        return None
    nodes = {n.id: n for n in graph.nodes if modeled(n)}
    out: dict[str, list[Transition]] = {}
    for t in graph.transitions:
        if modeled(t) and t.source_node_id in nodes and t.target_node_id in nodes:
            out.setdefault(t.source_node_id, []).append(t)
    enter = next((t for t in out.get(risk.affected_node_ids[0], [])
                  if t.target_node_id == branch), None)

    best: tuple[Decimal, list[Transition], str] | None = None
    budget = [2000]

    def better(p: Decimal, path: list[Transition]) -> bool:
        # Higher probability wins; on ties (e.g. unknown estimates) the shorter path wins.
        return best is None or (p, -len(path)) > (best[0], -len(best[1]))

    def walk(nid: str, counts: dict[str, int], path: list[Transition], p: Decimal) -> None:
        nonlocal best
        node = nodes[nid]
        if budget[0] <= 0 or len(path) > 50:
            return
        budget[0] -= 1
        if node.terminal:
            end = "goal" if node.kind == "goal" else "failure"
            if end != "goal" and better(p, path):
                best = (p, path, end)
            return
        for t in out.get(nid, []):
            iv = interval(t)
            q = p * (iv[1] if iv else ONE)
            if t.max_traversals is not None and counts.get(t.id, 0) >= t.max_traversals:
                if better(q, [*path, t]):
                    best = (q, [*path, t], "retries_exhausted")
                continue
            nxt = {**counts, t.id: counts.get(t.id, 0) + 1} if t.max_traversals else counts
            walk(t.target_node_id, nxt, [*path, t], q)

    walk(branch, {}, [], ONE)
    start = nodes[risk.affected_node_ids[0]] if risk.affected_node_ids[0] in nodes else None
    steps: list[CascadeStep] = []
    if start is not None:
        steps.append(CascadeStep(node_id=start.id, label=start.label, kind=start.kind))
    b = nodes[branch]

    def prob(t: Transition | None) -> Value | None:
        iv = interval(t) if t else None
        return _value(iv[0], iv[1], False) if iv else None

    steps.append(CascadeStep(node_id=b.id, label=b.label, kind=b.kind,
                             via_outcome=enter.outcome if enter else None,
                             via_trigger=enter.trigger if enter else None,
                             probability=prob(enter)))
    ends_in = "goal"
    if best is not None:
        _, path, ends_in = best
        for t in path:
            n = nodes[t.target_node_id]
            steps.append(CascadeStep(node_id=n.id, label=n.label, kind=n.kind,
                                     via_outcome=t.outcome, via_trigger=t.trigger,
                                     probability=prob(t)))
    parts = [f"{name}: {_describe(outcomes[key])}" for key, name in (
        ("goal", "still reach the goal"), ("failure", "end in failure"),
        ("retries_exhausted", "run out of retries"), ("unmodeled", "go somewhere not modeled"),
        ("truncated", "hit the analysis limits")) if key in outcomes]
    summary = "Once this happens, runs " + "; ".join(parts) + "." if parts else ""
    if any(v.status == "partial" for v in outcomes.values()):
        summary += " Add the missing estimates to get exact figures."
    return Cascade(steps=steps, ends_in=ends_in, outcomes_if_it_happens=outcomes,
                   summary=summary)


def explore(graph: ScenarioGraph, *, include_rejected: bool = False) -> Explorer:
    analyzable = _prepared(graph) is not None
    pool = [r for r in graph.risks if include_rejected or r.review_status != "rejected"]
    views: list[RiskView] = []
    for r in pool:
        branch = _branch_node(graph, r)
        lk = _likelihood(graph, r, branch, analyzable)
        views.append(RiskView(
            id=r.id, title=r.title, category=r.category, trigger=r.trigger,
            consequence=r.consequence, review_status=r.review_status, origin=r.origin,
            impact=r.impact, affected_node_ids=r.affected_node_ids, likelihood=lk,
            evidence=_evidence(graph, r), pinned_critical=r.impact == "critical",
            modeled_branch=branch is not None,
            cascade=_cascade(graph, r, branch) if branch and analyzable else None))

    views.sort(key=lambda v: (-IMPACT_TIER.get(v.impact or "", 0), -v.likelihood.worst_case,
                              -(v.likelihood.value.low if v.likelihood.value else ZERO),
                              v.title.casefold()))
    for i, v in enumerate(views, start=1):
        v.rank = i
        impact = {"critical": "Critical impact: always kept visible, whatever its likelihood.",
                  "high": "High impact.", "medium": "Medium impact.", "low": "Low impact."}
        v.reasons = [
            f"Ranked {i} of {len(views)}.",
            impact.get(v.impact or "", "Impact not set, so it ranks below rated risks."),
            v.likelihood.note,
            f"Evidence: {v.evidence.strength} ({v.evidence.count} item(s))." if v.evidence.count
            else "No supporting evidence linked yet.",
        ]
        if v.likelihood.basis != "modeled" and not v.modeled_branch:
            v.reasons.append("Its branch isn't on the map, so no cascade can be traced. "
                             "Accept it with its branch to model it.")
    return Explorer(risks=views, total=len(graph.risks),
                    excluded_rejected=len(graph.risks) - len(pool), analyzable=analyzable,
                    categories=sorted({r.category for r in graph.risks}))
