"""Turn observed outcomes into an estimate *candidate*. Nothing is applied automatically.

- Only relevant observations count: same decision and same link.
- Only comparable ones count: their context tags must match the context you ask
  about exactly (case-insensitive). Everything else is listed with the reason.
- Sparse data stays unresolved: below MIN_EXPOSURE eligible runs, no candidate.
- A candidate is a measured estimate: occurrences / exposure, with the count,
  denominator, date range and context recorded. Applying it needs a person's
  approval (see the API), and complete decisions still need explicit sibling
  changes so they keep totalling 100%.
"""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.analysis import check_groups
from pathguard_engine.graph import modeled
from pathguard_engine.model import EstimateChange, ObservedOutcome, ScenarioGraph
from pathguard_engine.plan import Issue
from pathguard_engine.probability import Estimate, MeasuredSource

MIN_EXPOSURE = 30
PLACES = Decimal("1e-10")


class Excluded(BaseModel):
    outcome_id: str
    reason: str


class Sibling(BaseModel):
    transition_id: str
    trigger: str
    current: str


class Proposal(BaseModel):
    transition_id: str
    decision_group_id: str | None
    status: Literal["candidate", "unresolved"]
    message: str
    context: list[str]
    used: list[str]
    excluded: list[Excluded]
    occurrences: int
    exposure: int
    date_from: date | None
    date_to: date | None
    comparability: str
    candidate: Estimate | None
    current: Estimate
    group_complete: bool
    siblings: list[Sibling]


def _tags(values: list[str]) -> frozenset[str]:
    return frozenset(v.strip().casefold() for v in values if v.strip())


def _label(e: Estimate) -> str:
    if e.kind == "point":
        s = f"{(e.value or Decimal(0)) * 100:f}"
        return (s.rstrip("0").rstrip(".") if "." in s else s) + "%"
    if e.kind == "range":
        return f"{e.low}–{e.high} (fraction)"
    return "unknown"


def propose(graph: ScenarioGraph, transition_id: str, outcomes: list[ObservedOutcome],
            context: list[str], *, min_exposure: int = MIN_EXPOSURE) -> Proposal | None:
    """The estimate candidate for one link, or None if the link isn't in the map."""
    t = next((x for x in graph.transitions if x.id == transition_id), None)
    if t is None:
        return None
    gid = t.decision_group_id
    group = next((g for g in graph.decision_groups if g.id == gid), None)
    want = _tags(context)
    used: list[ObservedOutcome] = []
    excluded: list[Excluded] = []
    for o in outcomes:
        if o.decision_group_id != gid:
            excluded.append(Excluded(outcome_id=o.id, reason="Recorded for a different decision."))
        elif o.transition_id != transition_id:
            excluded.append(Excluded(outcome_id=o.id, reason="Counts a different branch."))
        elif _tags(o.context) != want:
            have = ", ".join(sorted(_tags(o.context))) or "no tags"
            excluded.append(Excluded(outcome_id=o.id,
                                     reason=f"Different context ({have}), so not comparable."))
        else:
            used.append(o)

    occurrences = sum(o.occurrences for o in used)
    exposure = sum(o.exposure for o in used)
    starts = [o.period_start or o.observed_on for o in used]
    ends = [o.period_end or o.observed_on for o in used]
    shown = ", ".join(sorted(want)) or "no context tags"
    comparability = (f"Comparable when the observation is for the same decision and branch and "
                     f"its context tags are exactly: {shown}.")
    siblings = [Sibling(transition_id=x.id, trigger=x.trigger, current=_label(x.probability))
                for x in graph.transitions
                if gid and x.decision_group_id == gid and x.id != transition_id and modeled(x)]
    base = dict(transition_id=transition_id, decision_group_id=gid, context=sorted(want),
                used=[o.id for o in used], excluded=excluded, occurrences=occurrences,
                exposure=exposure, date_from=min(starts) if starts else None,
                date_to=max(ends) if ends else None, comparability=comparability,
                current=t.probability, group_complete=bool(group and group.complete),
                siblings=siblings)
    if not used:
        return Proposal(**base, status="unresolved", candidate=None,
                        message="No comparable observations yet, so the estimate stays as it is.")
    if exposure < min_exposure:
        return Proposal(**base, status="unresolved", candidate=None,
                        message=f"Too little data: {exposure} eligible run(s) observed, fewer than "
                                f"the {min_exposure} needed. The estimate stays as it is.")
    value = (Decimal(occurrences) / Decimal(exposure)).quantize(PLACES, rounding=ROUND_HALF_EVEN)
    value = value.normalize() if value else Decimal(0)  # 0.9250000000 -> 0.925
    candidate = Estimate(kind="point", value=value, source=MeasuredSource(
        data_source=f"PathGuard observed outcomes ({len(used)} record(s))",
        count=occurrences, denominator=exposure, date_from=min(starts), date_to=max(ends),
        context=shown))
    return Proposal(**base, status="candidate", candidate=candidate,
                    message=f"{occurrences} of {exposure} eligible runs took this branch "
                            f"({_label(candidate)}). Review it before applying.")


def apply_estimates(graph: ScenarioGraph, changes: list[EstimateChange]
                    ) -> tuple[ScenarioGraph, list[Issue]]:
    """Set the given estimates; report decisions that are no longer valid. No rebalancing."""
    by_id = {c.transition_id: c.new_probability for c in changes}
    new = graph.model_copy(update={"transitions": [
        t.model_copy(update={"probability": by_id[t.id]}) if t.id in by_id else t
        for t in graph.transitions]})
    labels = {t.id: t.trigger for t in new.transitions}
    issues = []
    for g in check_groups(new):
        if g.status == "invalid":
            members = [t.id for t in new.transitions
                       if t.source_node_id == g.node_id and modeled(t) and t.id not in by_id]
            hint = (" Change these estimates explicitly too: "
                    + ", ".join(f"“{labels[m]}”" for m in members) + ".") if members else ""
            issues.append(Issue(code="decision_invalid", severity="error",
                                message=g.message + hint, refs=[g.node_id, *members]))
    return new, issues
