"""Probability analysis of a modeled scenario map.

Rules (see README "Probability engine"):
- A branch probability is conditional on reaching its decision state.
- Exact results need point estimates everywhere they matter. Ranges give outer
  bounds only (each estimate anywhere in its range, independently); midpoints
  are never used. Missing estimates are never filled in: results depending on
  them are "partial" and report only what known estimates guarantee.
- A complete, exclusive decision must total exactly 100% (or, with ranges, be
  able to). Incomplete decisions keep their unmodeled share visible.
- Runs are followed state by state, where the state includes how many times
  each attempt-limited link has been taken, so reconverging paths are not
  double counted and retries are bounded. A run that would exceed a link's
  attempt limit ends as "retries exhausted".
- Depth, expansions and listed paths are capped; caps are reported.

Pure Python + Decimal; no I/O.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.graph import modeled, validate_graph
from pathguard_engine.model import Plan, ScenarioGraph, ScenarioNode, Transition
from pathguard_engine.plan import Issue

ZERO, ONE = Decimal(0), Decimal(1)
Interval = tuple[Decimal, Decimal]
Bucket = Literal["goal", "failure", "residual", "retries_exhausted", "unmodeled", "truncated"]
BUCKETS: tuple[Bucket, ...] = (
    "goal", "failure", "residual", "retries_exhausted", "unmodeled", "truncated")

ASSUMPTIONS = [
    "Each branch probability is conditional on reaching its decision under the stated "
    "context and controls.",
    "Ranges give outer bounds: every estimate may lie anywhere in its range, independently. "
    "Bounds are not forecasts and no midpoint is used.",
    "When a link's attempt limit is reached, a run that would take it again ends as "
    "\"retries exhausted\".",
    "Missing estimates are never filled in. Results that depend on them are partial and show "
    "only what the known estimates guarantee.",
    "Clean first-pass success counts only runs that reach the goal through success links, "
    "with no failure, delay or recovery.",
]


# --------------------------------------------------------------------------- results


class Value(BaseModel):
    status: Literal["exact", "bounds", "partial"]
    value: Decimal | None = None  # exact only
    low: Decimal  # partial: the guaranteed minimum
    high: Decimal
    note: str = ""


class GroupCheck(BaseModel):
    node_id: str
    group_id: str | None  # None: a single link with no decision group
    label: str
    status: Literal["ok", "unresolved", "invalid"]
    complete: bool
    total_low: Decimal
    total_high: Decimal
    missing: list[str] = []
    unmodeled_low: Decimal = ZERO
    unmodeled_high: Decimal = ZERO
    message: str = ""


class PathResult(BaseModel):
    transitions: list[str]
    nodes: list[str]
    labels: list[str]
    probability: Value
    ends_in: Bucket
    uses_recovery: bool


class Limits(BaseModel):
    max_depth: int
    max_expansions: int
    max_results: int
    expansions_used: int
    truncated: bool
    paths_found: int
    paths_shown: int
    paths_omitted: int
    path_search_truncated: bool


class Analysis(BaseModel):
    status: Literal["exact", "bounds", "partial", "blocked"]
    blockers: list[str] = []
    first_pass: Value | None = None
    eventual_success: Value | None = None
    outcomes: dict[str, Value] = {}
    groups: list[GroupCheck] = []
    missing_estimates: list[str] = []
    paths: list[PathResult] = []
    limits: Limits
    assumptions: list[str] = ASSUMPTIONS
    issues: list[Issue] = []


# --------------------------------------------------------------------------- helpers


def interval(t: Transition) -> Interval | None:
    p = t.probability
    if p.kind == "point":
        return (p.value, p.value)  # type: ignore[return-value]
    if p.kind == "range":
        return (p.low, p.high)  # type: ignore[return-value]
    return None


def _pct(d: Decimal) -> str:
    s = f"{d * 100:f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) + "%"


def _value(lo: Decimal, hi: Decimal, partial: bool, note: str = "") -> Value:
    hi = min(hi, ONE)
    if partial:
        return Value(status="partial", low=lo, high=hi, note=note)
    if lo == hi:
        return Value(status="exact", value=lo, low=lo, high=hi, note=note)
    return Value(status="bounds", low=lo, high=hi, note=note)


@dataclass
class _Model:
    nodes: dict[str, ScenarioNode]
    out: dict[str, list[Transition]]
    unmodeled: dict[str, Interval]  # per decision node, from group checks
    start: str | None
    goal_ids: set[str]


def _model(graph: ScenarioGraph) -> _Model:
    nodes = {n.id: n for n in graph.nodes if modeled(n)}
    out: dict[str, list[Transition]] = defaultdict(list)
    for t in graph.transitions:
        if modeled(t) and t.source_node_id in nodes and t.target_node_id in nodes:
            out[t.source_node_id].append(t)
    starts = [n.id for n in nodes.values() if n.kind == "start"]
    return _Model(nodes, out, {}, starts[0] if len(starts) == 1 else None,
                  {n.id for n in nodes.values() if n.kind == "goal"})


# --------------------------------------------------------------------------- decisions


def check_groups(graph: ScenarioGraph) -> list[GroupCheck]:
    """Check every modeled decision point: totals, completeness, exclusivity."""
    m = _model(graph)
    groups = {g.id: g for g in graph.decision_groups}
    checks: list[GroupCheck] = []
    for node_id, exits in sorted(m.out.items(), key=lambda kv: kv[0]):
        node = m.nodes[node_id]
        if node.terminal or not exits:
            continue
        gid = exits[0].decision_group_id
        group = groups.get(gid) if gid else None
        label = group.label if group else f"After “{node.label}”"
        complete = bool(group and group.complete)
        known = [iv for t in exits if (iv := interval(t)) is not None]
        missing = [t.id for t in exits if interval(t) is None]
        lo = sum((iv[0] for iv in known), ZERO)
        hi = sum((iv[1] for iv in known), ZERO)
        check = GroupCheck(node_id=node_id, group_id=gid, label=label, status="ok",
                           complete=complete, total_low=lo, total_high=hi, missing=missing)
        if group and not group.exclusive:
            check.status = "invalid"
            check.message = (f"Branches in “{label}” can overlap (not exclusive), so their "
                             "probabilities can't be combined.")
        elif lo > ONE:
            check.status = "invalid"
            check.message = f"Branches in “{label}” add up to more than 100% ({_pct(lo)})."
        elif complete and missing:
            check.status = "unresolved"
            check.message = f"{len(missing)} branch(es) in “{label}” have no estimate yet."
        elif complete and lo == hi and lo != ONE:
            check.status = "invalid"
            check.message = (f"Branches in “{label}” add up to {_pct(lo)}. A complete decision "
                             "must total exactly 100%: adjust the estimates, add the missing "
                             "outcome, or mark the decision incomplete.")
        elif complete and not lo <= ONE <= hi:
            check.status = "invalid"
            check.message = (f"No combination within the ranges in “{label}” adds up to 100% "
                             f"(possible totals {_pct(lo)}–{_pct(hi)}).")
        elif not complete:
            check.unmodeled_low = max(ZERO, ONE - hi) if not missing else ZERO
            check.unmodeled_high = max(ZERO, ONE - lo)
            if missing or check.unmodeled_high > 0:
                check.status = "unresolved"
                parts = []
                if missing:
                    parts.append(f"{len(missing)} branch(es) have no estimate")
                if check.unmodeled_high > 0:
                    share = _pct(check.unmodeled_high) if check.unmodeled_low == \
                        check.unmodeled_high else \
                        f"{_pct(check.unmodeled_low)}–{_pct(check.unmodeled_high)}"
                    parts.append(f"{share} of outcomes are not modeled")
                check.message = f"“{label}”: " + "; ".join(parts) + "."
        checks.append(check)
    return checks


# --------------------------------------------------------------------------- runs


@dataclass
class _Run:
    m: _Model
    success_only: bool
    max_depth: int
    max_expansions: int
    expansions: int = 0
    truncated: bool = False
    missing: set[str] = field(default_factory=set)
    memo: dict = field(default_factory=dict)
    stack: set = field(default_factory=set)
    stop_at: frozenset[str] = frozenset()  # reaching one of these ends the run as "hit"

    def visit(self, node_id: str, counts: tuple[tuple[str, int], ...],
              depth: int) -> dict[str, Interval]:
        node = self.m.nodes[node_id]
        if node_id in self.stop_at:
            return {"hit": (ONE, ONE)}
        if node.terminal:
            end = "goal" if node.kind == "goal" else \
                "residual" if node.kind == "residual" else "failure"
            return {end: (ONE, ONE)}
        key = (node_id, counts)
        if key in self.memo:
            return self.memo[key]
        if depth >= self.max_depth or self.expansions >= self.max_expansions \
                or key in self.stack:
            self.truncated = True
            return {"truncated": (ONE, ONE)}
        self.expansions += 1
        self.stack.add(key)
        acc: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
        used = dict(counts)
        for t in self.m.out.get(node_id, []):
            if self.success_only and t.outcome != "success":
                continue
            iv = interval(t)
            if iv is None:
                self.missing.add(t.id)
                iv = (ZERO, ONE)
            if t.max_traversals is not None and used.get(t.id, 0) >= t.max_traversals:
                child = {"retries_exhausted": (ONE, ONE)}
            else:
                nxt = counts
                if t.max_traversals is not None:
                    nxt = tuple(sorted({**used, t.id: used.get(t.id, 0) + 1}.items()))
                child = self.visit(t.target_node_id, nxt, depth + 1)
            for bucket, (lo, hi) in child.items():
                acc[bucket][0] += iv[0] * lo
                acc[bucket][1] += iv[1] * hi
        if not self.success_only:
            u_lo, u_hi = self.m.unmodeled.get(node_id, (ZERO, ZERO))
            if u_hi > 0:
                acc["unmodeled"][0] += u_lo
                acc["unmodeled"][1] += u_hi
        self.stack.discard(key)
        result = {b: (lo, min(hi, ONE)) for b, (lo, hi) in acc.items()}
        self.memo[key] = result
        return result


def _paths(m: _Model, max_depth: int, max_expansions: int, max_results: int):
    found: list[PathResult] = []
    tmap = {t.id: t for ts in m.out.values() for t in ts}
    expansions = 0
    truncated = False
    total = 0

    def walk(node_id, counts, nodes, trans, lo, hi, partial):
        nonlocal expansions, truncated, total
        node = m.nodes[node_id]
        if node.terminal:
            end = "goal" if node.kind == "goal" else \
                "residual" if node.kind == "residual" else "failure"
            record(end, nodes, trans, lo, hi, partial)
            return
        if len(trans) >= max_depth or expansions >= max_expansions:
            truncated = True
            return
        expansions += 1
        for t in m.out.get(node_id, []):
            iv = interval(t)
            p_lo, p_hi, part = (iv[0], iv[1], partial) if iv else (ZERO, ONE, True)
            if t.max_traversals is not None and counts.get(t.id, 0) >= t.max_traversals:
                record("retries_exhausted", nodes, [*trans, t.id], lo * p_lo, hi * p_hi, part)
                continue
            nxt = {**counts, t.id: counts.get(t.id, 0) + 1} if t.max_traversals else counts
            walk(t.target_node_id, nxt, [*nodes, t.target_node_id], [*trans, t.id],
                 lo * p_lo, hi * p_hi, part)

    def record(end, nodes, trans, lo, hi, partial):
        nonlocal total
        total += 1
        found.append(PathResult(
            transitions=trans, nodes=nodes, labels=[m.nodes[n].label for n in nodes],
            probability=_value(lo, hi, partial), ends_in=end,
            uses_recovery=any(tmap[t].outcome == "recovery" for t in trans)))

    if m.start:
        walk(m.start, {}, [m.start], [], ONE, ONE, False)
    found.sort(key=lambda p: (p.probability.high, p.probability.low), reverse=True)
    return found[:max_results], total, truncated


# --------------------------------------------------------------------------- entry point


def analyze(graph: ScenarioGraph, plan: Plan | None = None, *, max_depth: int = 200,
            max_expansions: int = 20000, max_results: int = 10) -> Analysis:
    """Probability analysis of the modeled map. Never raises on user data."""
    structural = [i for i in validate_graph(graph, plan) if i.severity == "error"]
    groups = check_groups(graph)
    invalid = [g for g in groups if g.status == "invalid"]
    empty_limits = Limits(max_depth=max_depth, max_expansions=max_expansions,
                          max_results=max_results, expansions_used=0, truncated=False,
                          paths_found=0, paths_shown=0, paths_omitted=0,
                          path_search_truncated=False)
    issues = structural + [Issue(code="decision_invalid", severity="error", message=g.message,
                                 refs=[g.node_id] + ([g.group_id] if g.group_id else []))
                           for g in invalid]
    if not graph.nodes or structural or invalid:
        blockers = []
        if not graph.nodes:
            blockers.append("No scenario map yet. Generate one from the plan first.")
        if structural:
            blockers.append(f"{len(structural)} map problem(s) must be fixed first.")
        if invalid:
            blockers.append(f"{len(invalid)} decision(s) have probabilities that can't be "
                            "valid. Fix them before analyzing.")
        return Analysis(status="blocked", blockers=blockers, groups=groups,
                        limits=empty_limits, issues=issues)

    m = _model(graph)
    m.unmodeled = {g.node_id: (g.unmodeled_low, g.unmodeled_high) for g in groups
                   if g.unmodeled_high > 0}
    full = _Run(m, success_only=False, max_depth=max_depth, max_expansions=max_expansions)
    buckets = full.visit(m.start, (), 0) if m.start else {}
    first = _Run(m, success_only=True, max_depth=max_depth, max_expansions=max_expansions)
    fp = first.visit(m.start, (), 0) if m.start else {}

    unmodeled = buckets.get("unmodeled", (ZERO, ZERO))
    partial = bool(full.missing) or full.truncated or unmodeled[1] > 0
    reasons = []
    if full.missing:
        reasons.append(f"{len(full.missing)} link(s) have no estimate")
    if unmodeled[1] > 0:
        reasons.append("some outcomes are not modeled")
    if full.truncated:
        reasons.append("the analysis hit its depth or expansion limit")
    note = ("Partial: " + ", ".join(reasons) + ". Only the guaranteed minimum is reliable."
            if partial else "")

    fp_partial = bool(first.missing) or first.truncated
    fp_goal = fp.get("goal", (ZERO, ZERO))
    goal = buckets.get("goal", (ZERO, ZERO))
    outcomes = {b: _value(*buckets[b], partial) for b in BUCKETS if b in buckets}
    eventual = _value(*goal, partial, note)
    first_pass = _value(*fp_goal, fp_partial,
                        "Partial: a success link on the way has no estimate." if fp_partial else "")

    paths, found, path_trunc = _paths(m, max_depth, max_expansions, max_results)
    limits = Limits(max_depth=max_depth, max_expansions=max_expansions, max_results=max_results,
                    expansions_used=full.expansions, truncated=full.truncated, paths_found=found,
                    paths_shown=len(paths), paths_omitted=max(0, found - len(paths)),
                    path_search_truncated=path_trunc)
    unresolved = [Issue(code="decision_unresolved", severity="warning", message=g.message,
                        refs=[g.node_id] + g.missing) for g in groups if g.status == "unresolved"]
    return Analysis(
        status=eventual.status, first_pass=first_pass, eventual_success=eventual,
        outcomes=outcomes, groups=groups, missing_estimates=sorted(full.missing | first.missing),
        paths=paths, limits=limits, issues=unresolved)


# --------------------------------------------------------------------------- per-node questions


def _prepared(graph: ScenarioGraph) -> _Model | None:
    """The analyzable model, or None when the map can't be analyzed yet."""
    if any(i.severity == "error" for i in validate_graph(graph)):
        return None
    groups = check_groups(graph)
    if any(g.status == "invalid" for g in groups):
        return None
    m = _model(graph)
    m.unmodeled = {g.node_id: (g.unmodeled_low, g.unmodeled_high) for g in groups
                   if g.unmodeled_high > 0}
    return m


def reach_probability(graph: ScenarioGraph, node_id: str, *, max_depth: int = 200,
                      max_expansions: int = 20000) -> Value | None:
    """Chance that a run reaches `node_id` at least once (None if not analyzable)."""
    m = _prepared(graph)
    if m is None or m.start is None or node_id not in m.nodes:
        return None
    run = _Run(m, success_only=False, max_depth=max_depth, max_expansions=max_expansions,
               stop_at=frozenset({node_id}))
    buckets = run.visit(m.start, (), 0)
    hit = buckets.get("hit", (ZERO, ZERO))
    partial = bool(run.missing) or run.truncated or buckets.get("unmodeled", (ZERO, ZERO))[1] > 0
    return _value(*hit, partial,
                  "Partial: missing estimates or unmodeled outcomes could change this."
                  if partial else "")


def outcomes_from(graph: ScenarioGraph, node_id: str, *, max_depth: int = 200,
                  max_expansions: int = 20000) -> dict[str, Value] | None:
    """How runs end once they reach `node_id` (attempt counts start fresh there)."""
    m = _prepared(graph)
    if m is None or node_id not in m.nodes:
        return None
    run = _Run(m, success_only=False, max_depth=max_depth, max_expansions=max_expansions)
    buckets = run.visit(node_id, (), 0)
    partial = bool(run.missing) or run.truncated or buckets.get("unmodeled", (ZERO, ZERO))[1] > 0
    return {b: _value(*buckets[b], partial) for b in BUCKETS if b in buckets}
