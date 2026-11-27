"""Baseline vs variant: apply a mitigation to a copy, then compare the two.

- `apply_mitigation` never edits the baseline. It records the mitigation, applies
  its explicit estimate changes, optional added branches and an owner change.
  It never rebalances a decision: if a complete decision no longer totals 100%,
  it reports which sibling estimates must be changed explicitly.
- `compare` reports both sides and the differences. Deltas in percentage points
  are given only when both sides are exact; otherwise the comparison is flagged.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.analysis import Value, analyze, check_groups
from pathguard_engine.graph import modeled, validate_graph
from pathguard_engine.model import (
    DecisionGroup,
    Mitigation,
    Plan,
    ScenarioGraph,
    ScenarioNode,
    Transition,
)
from pathguard_engine.plan import Issue
from pathguard_engine.probability import Estimate

HUNDRED = Decimal(100)


# --------------------------------------------------------------------------- apply


def apply_mitigation(plan: Plan, graph: ScenarioGraph, mitigation: Mitigation, *,
                     add_nodes: list[ScenarioNode] | None = None,
                     add_transitions: list[Transition] | None = None,
                     add_groups: list[DecisionGroup] | None = None,
                     ) -> tuple[Plan, ScenarioGraph, list[Issue]]:
    """A new (plan, graph) with the mitigation applied, plus blocking issues if any."""
    issues: list[Issue] = []
    if any(m.id == mitigation.id for m in graph.mitigations):
        issues.append(Issue(code="mitigation_duplicate", severity="error",
                            message="This mitigation is already applied.", refs=[mitigation.id]))
    changes = {c.transition_id: c.new_probability for c in mitigation.estimate_changes}
    transitions = [*graph.transitions, *(add_transitions or [])]
    known = {t.id for t in transitions}
    for tid in changes.keys() - known:
        issues.append(Issue(code="dangling_reference", severity="error",
                            message="An estimate change points to a link that doesn't exist.",
                            refs=[mitigation.id, tid]))
    transitions = [t.model_copy(update={"probability": changes[t.id]}) if t.id in changes else t
                   for t in transitions]
    new_graph = graph.model_copy(update={
        "nodes": [*graph.nodes, *(add_nodes or [])],
        "transitions": transitions,
        "decision_groups": [*graph.decision_groups, *(add_groups or [])],
        "mitigations": [*graph.mitigations, mitigation],
    })

    new_plan = plan
    if mitigation.target_kind == "task" and mitigation.owner:
        if not any(t.id == mitigation.target_id for t in plan.tasks):
            issues.append(Issue(code="dangling_reference", severity="error",
                                message="The mitigation's task doesn't exist.",
                                refs=[mitigation.id, mitigation.target_id]))
        new_plan = plan.model_copy(update={"tasks": [
            t.model_copy(update={"owner": mitigation.owner}) if t.id == mitigation.target_id
            else t for t in plan.tasks]})

    issues += [i for i in validate_graph(new_graph, new_plan) if i.severity == "error"]
    labels = {t.id: t.trigger for t in transitions}
    for g in check_groups(new_graph):
        if g.status != "invalid":
            continue
        members = [t.id for t in transitions if t.source_node_id == g.node_id and modeled(t)]
        untouched = [m for m in members if m not in changes]
        hint = (" Change these estimates explicitly too: "
                + ", ".join(f"“{labels[m]}”" for m in untouched) + "."
                if untouched and any(m in changes for m in members) else "")
        issues.append(Issue(code="decision_invalid", severity="error",
                            message=g.message + hint, refs=[g.node_id, *untouched]))
    return new_plan, new_graph, issues


# --------------------------------------------------------------------------- compare


class Side(BaseModel):
    status: Literal["exact", "bounds", "partial", "blocked"]
    first_pass: Value | None
    eventual_success: Value | None
    severe_risks: list[str]
    open_assumptions: list[str]
    hypothetical_estimates: int
    missing_estimates: int
    mitigation_cost: Decimal | None
    mitigation_delay_days: Decimal | None
    task_cost: Decimal | None
    task_days: Decimal | None
    mitigations: list[str]


class Delta(BaseModel):
    comparable: bool
    pp: Decimal | None = None  # exact difference in percentage points
    low_pp: Decimal | None = None
    high_pp: Decimal | None = None
    note: str


class BranchChange(BaseModel):
    transition_id: str
    label: str
    change: Literal["added", "removed", "changed"]
    before: str | None
    after: str | None
    fields: list[str]


class Comparison(BaseModel):
    baseline: Side
    variant: Side
    first_pass_delta: Delta
    eventual_delta: Delta
    changed_branches: list[BranchChange]
    added_nodes: list[str]
    removed_nodes: list[str]
    flags: list[str]


def _pct(d: Decimal) -> str:
    s = f"{d * HUNDRED:f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) + "%"


def estimate_text(e: Estimate) -> str:
    if e.kind == "unknown":
        return "unknown"
    num = _pct(e.value) if e.kind == "point" else f"{_pct(e.low)}–{_pct(e.high)}"  # type: ignore[arg-type]
    return f"{num} ({e.source.type})"


def _total(values: list[Decimal | None]) -> Decimal | None:
    known = [v for v in values if v is not None]
    return sum(known, Decimal(0)) if known else None


def _side(plan: Plan, graph: ScenarioGraph) -> Side:
    a = analyze(graph, plan)
    live = [t for t in graph.transitions if modeled(t)]
    return Side(
        status=a.status, first_pass=a.first_pass, eventual_success=a.eventual_success,
        severe_risks=[r.title for r in graph.risks
                      if r.impact in ("critical", "high") and r.review_status != "rejected"],
        open_assumptions=[x.text for x in graph.assumptions if x.status == "open"]
        + [a for m in graph.mitigations for a in m.assumptions],
        hypothetical_estimates=sum(1 for t in live if t.probability.source.type == "hypothetical"),
        missing_estimates=len(a.missing_estimates),
        mitigation_cost=_total([m.cost for m in graph.mitigations]),
        mitigation_delay_days=_total([m.delay_days for m in graph.mitigations]),
        task_cost=_total([t.cost for t in plan.tasks]),
        task_days=_total([t.duration_days for t in plan.tasks]),
        mitigations=[m.title for m in graph.mitigations])


def _clean(d: Decimal) -> Decimal:
    """Drop trailing zeros without switching to exponent notation (8.55000 -> 8.55)."""
    n = d.normalize()
    return n.quantize(Decimal(1)) if n.as_tuple().exponent > 0 else n  # type: ignore[operator]


def _delta(b: Value | None, v: Value | None, what: str) -> Delta:
    if b is None or v is None:
        return Delta(comparable=False, note=f"{what} can't be calculated for both versions.")
    if b.status == "exact" and v.status == "exact":
        pp = _clean((v.value - b.value) * HUNDRED)  # type: ignore[operator]
        return Delta(comparable=True, pp=pp, low_pp=pp, high_pp=pp,
                     note=f"{what} changes by {pp:+f} percentage points.")
    if "partial" in (b.status, v.status):
        return Delta(comparable=False, note=f"{what} is partial in at least one version "
                     "(missing estimates or unmodeled outcomes), so no difference is shown.")
    lo, hi = _clean((v.low - b.high) * HUNDRED), _clean((v.high - b.low) * HUNDRED)
    return Delta(comparable=False, low_pp=lo, high_pp=hi,
                 note=f"{what} uses ranges: the difference lies between {lo:+f} and {hi:+f} "
                      "percentage points. No single figure is given.")


def compare(baseline: tuple[Plan, ScenarioGraph], variant: tuple[Plan, ScenarioGraph]
            ) -> Comparison:
    (bp, bg), (vp, vg) = baseline, variant
    b, v = _side(bp, bg), _side(vp, vg)

    bt = {t.id: t for t in bg.transitions if modeled(t)}
    vt = {t.id: t for t in vg.transitions if modeled(t)}
    names = {n.id: n.label for n in [*bg.nodes, *vg.nodes]}

    def label(t: Transition) -> str:
        return f"{names.get(t.source_node_id, '?')} → {names.get(t.target_node_id, '?')} " \
               f"({t.trigger})"

    changes: list[BranchChange] = []
    for tid in sorted(vt.keys() - bt.keys()):
        changes.append(BranchChange(transition_id=tid, label=label(vt[tid]), change="added",
                                    before=None, after=estimate_text(vt[tid].probability),
                                    fields=[]))
    for tid in sorted(bt.keys() - vt.keys()):
        changes.append(BranchChange(transition_id=tid, label=label(bt[tid]), change="removed",
                                    before=estimate_text(bt[tid].probability), after=None,
                                    fields=[]))
    for tid in sorted(bt.keys() & vt.keys()):
        x, y = bt[tid], vt[tid]
        fields = [f for f in ("probability", "outcome", "target_node_id", "max_traversals",
                              "cost", "delay_days", "trigger")
                  if getattr(x, f) != getattr(y, f)]
        if fields:
            changes.append(BranchChange(transition_id=tid, label=label(y), change="changed",
                                        before=estimate_text(x.probability),
                                        after=estimate_text(y.probability), fields=fields))

    bn = {n.id for n in bg.nodes if modeled(n)}
    vn = {n.id for n in vg.nodes if modeled(n)}
    flags: list[str] = []
    added, removed = sorted(vn - bn), sorted(bn - vn)
    structural = [c for c in changes if c.change != "changed"]
    if added or removed or structural:
        flags.append(f"Different coverage: the variant adds {len(added)} and removes "
                     f"{len(removed)} step(s), and adds or removes {len(structural)} link(s). "
                     "Its figures include outcomes the baseline doesn't model.")
    for name, side in (("Baseline", b), ("Variant", v)):
        if side.status in ("partial", "blocked"):
            flags.append(f"{name} has unresolved inputs ({side.status}): "
                         f"{side.missing_estimates} link(s) without estimates.")
    if b.hypothetical_estimates or v.hypothetical_estimates:
        flags.append("Hypothetical estimates are involved; the difference shows the effect of "
                     "the stated assumptions, not a measured result.")
    return Comparison(
        baseline=b, variant=v,
        first_pass_delta=_delta(b.first_pass, v.first_pass, "Clean first-pass success"),
        eventual_delta=_delta(b.eventual_success, v.eventual_success, "Eventual success"),
        changed_branches=changes, added_nodes=added, removed_nodes=removed, flags=flags)
