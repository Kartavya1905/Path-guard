"""Report building blocks: risk register CSV, estimate sources, evidence gaps, review status.

CSV cells are escaped by the csv module (quotes, commas, newlines) and protected
against spreadsheet formula injection: a cell starting with =, +, -, @, tab or
carriage return is prefixed with an apostrophe so spreadsheet apps show it as text.
"""

import csv
import io
from collections import Counter
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.explorer import Explorer
from pathguard_engine.graph import modeled
from pathguard_engine.model import ScenarioGraph

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

REGISTER_COLUMNS = [
    "Report status", "Rank", "Risk", "Category", "Review status", "Impact", "Likelihood",
    "Likelihood basis", "Evidence strength", "Trigger", "Consequence", "Suggested mitigation",
    "Mitigations (owner)", "Affected steps",
]


def safe_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def _pct(fraction: str) -> str:
    from decimal import Decimal

    s = f"{Decimal(fraction) * 100:f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) + "%"


def risk_register_rows(graph: ScenarioGraph, explorer: Explorer, status_label: str
                       ) -> list[list[str]]:
    nodes = {n.id: n.label for n in graph.nodes}
    hints = {r.id: r.mitigation_hint for r in graph.risks}
    rows = []
    for r in explorer.risks:
        v = r.likelihood.value
        likelihood = "unknown" if v is None else (
            _pct(str(v.value)) if v.status == "exact" else
            f"{_pct(str(v.low))}-{_pct(str(v.high))}" if v.status == "bounds" else
            f"at least {_pct(str(v.low))}" if v.low else "unknown")
        mitigations = "; ".join(
            f"{m.title} ({m.owner or 'no owner'})" for m in graph.mitigations
            if (m.target_kind, m.target_id) in {("risk", r.id)} | {
                ("node", n) for n in r.affected_node_ids})
        rows.append([status_label, str(r.rank), r.title, r.category.replace("_", " "),
                     r.review_status, r.impact or "not set", likelihood, r.likelihood.basis,
                     r.evidence.strength, r.trigger, r.consequence, hints.get(r.id) or "",
                     mitigations, "; ".join(nodes.get(n, n) for n in r.affected_node_ids)])
    return rows


def risk_register_csv(graph: ScenarioGraph, explorer: Explorer, status_label: str) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(REGISTER_COLUMNS)
    for row in risk_register_rows(graph, explorer, status_label):
        writer.writerow([safe_cell(c) for c in row])
    return out.getvalue()


# --------------------------------------------------------------------------- report sections


class EstimateSource(BaseModel):
    transition_id: str
    link: str
    estimate: str
    source: Literal["measured", "expert", "hypothetical", "unknown"]
    detail: str
    verification: str


class EvidenceGap(BaseModel):
    kind: Literal["risk_without_evidence", "estimate_without_evidence", "missing_estimate"]
    target_id: str
    message: str


def _source_detail(s: object) -> str:
    kind = getattr(s, "type", "unknown")
    g = lambda name: getattr(s, name, "")  # noqa: E731
    if kind == "measured":
        return (f"{g('data_source')}: {g('count')} of {g('denominator')}, "
                f"{g('date_from')} to {g('date_to')} ({g('context')})")
    if kind == "expert":
        return f"{g('reviewer')} on {g('review_date')}: {g('rationale')}"
    if kind == "hypothetical":
        return f"Hypothetical: {g('assumption')}"
    return "No estimate yet"


def estimate_sources(graph: ScenarioGraph) -> list[EstimateSource]:
    nodes = {n.id: n.label for n in graph.nodes}
    out = []
    for t in graph.transitions:
        if not modeled(t):
            continue
        e, s = t.probability, t.probability.source
        if e.kind == "point":
            text = _pct(str(e.value))
        elif e.kind == "range":
            text = f"{_pct(str(e.low))}–{_pct(str(e.high))}"
        else:
            text = "unknown"
        detail = _source_detail(s)
        verified = f"reviewed by {e.reviewed_by} on {e.reviewed_on}" \
            if e.verification == "reviewed" else "unverified"
        out.append(EstimateSource(
            transition_id=t.id, link=f"{nodes.get(t.source_node_id, '?')} → "
                                     f"{nodes.get(t.target_node_id, '?')} ({t.trigger})",
            estimate=text, source=s.type, detail=detail, verification=verified))
    return out


def evidence_gaps(graph: ScenarioGraph) -> list[EvidenceGap]:
    linked = {(link.target_kind, link.target_id) for link in graph.evidence_links}
    gaps: list[EvidenceGap] = []
    for r in graph.risks:
        if r.review_status == "rejected":
            continue
        if ("risk", r.id) not in linked and not any(("node", n) in linked
                                                     for n in r.affected_node_ids):
            gaps.append(EvidenceGap(kind="risk_without_evidence", target_id=r.id,
                                    message=f"Risk “{r.title}” has no supporting evidence."))
    for t in graph.transitions:
        if not modeled(t):
            continue
        if t.probability.kind == "unknown":
            gaps.append(EvidenceGap(kind="missing_estimate", target_id=t.id,
                                    message=f"“{t.trigger}” has no probability estimate."))
        elif t.probability.source.type != "measured" and ("transition", t.id) not in linked:
            gaps.append(EvidenceGap(
                kind="estimate_without_evidence", target_id=t.id,
                message=f"“{t.trigger}” rests on a {t.probability.source.type} estimate with "
                        "no linked evidence."))
    return gaps


def review_summary(graph: ScenarioGraph) -> dict[str, dict[str, int]]:
    return {
        "risks": dict(Counter(r.review_status for r in graph.risks)),
        "steps": dict(Counter(n.review_status for n in graph.nodes)),
        "links": dict(Counter(t.review_status for t in graph.transitions)),
    }
