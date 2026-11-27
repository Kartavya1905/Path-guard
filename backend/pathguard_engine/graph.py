"""Scenario-graph validation and analysis readiness.

`validate_graph` never raises on user data: it returns issues, so an
incomplete draft can still be saved while the UI explains what blocks
analysis or review.

Only *modeled* items take part in structural checks: nodes, transitions and
decision groups whose review status is "accepted" or "edited". Suggested and
rejected items are still checked for broken references, because they are
stored and may be accepted later.
"""

from collections import defaultdict

from pydantic import BaseModel

from pathguard_engine.model import Plan, ScenarioGraph, ScenarioNode, Transition
from pathguard_engine.plan import Issue, validate_plan

MODELED = frozenset({"accepted", "edited"})


def modeled(item: ScenarioNode | Transition) -> bool:
    return item.review_status in MODELED


def validate_graph(graph: ScenarioGraph, plan: Plan | None = None) -> list[Issue]:
    issues: list[Issue] = []
    issues += _duplicates(graph)
    issues += _references(graph, plan)
    issues += _structure(graph)
    if plan is not None:
        issues += _order_conflicts(graph, plan)
    return issues


# --------------------------------------------------------------------------- references


def _duplicates(g: ScenarioGraph) -> list[Issue]:
    seen: set[str] = set()
    issues = []
    for coll in (g.nodes, g.transitions, g.decision_groups, g.risks, g.evidence,
                 g.evidence_links, g.assumptions, g.mitigations, g.notes):
        for item in coll:
            if item.id in seen:
                issues.append(Issue(code="duplicate_id", severity="error",
                                    message=f"The ID {item.id} is used more than once.",
                                    refs=[item.id]))
            seen.add(item.id)
    return issues


def _references(g: ScenarioGraph, plan: Plan | None) -> list[Issue]:
    nodes = {n.id for n in g.nodes}
    transitions = {t.id: t for t in g.transitions}
    groups = {d.id for d in g.decision_groups}
    tasks = {t.id for t in plan.tasks} if plan is not None else None
    targets: dict[str, set[str]] = {
        "node": nodes, "transition": set(transitions), "decision_group": groups,
        "risk": {r.id for r in g.risks}, "mitigation": {m.id for m in g.mitigations},
        "task": tasks if tasks is not None else set(),
    }
    issues: list[Issue] = []

    def dangling(owner: str, what: str, ref: str) -> None:
        issues.append(Issue(code="dangling_reference", severity="error",
                            message=f"{what} points to something that no longer exists ({ref}).",
                            refs=[owner, ref]))

    for t in g.transitions:
        for end in (t.source_node_id, t.target_node_id):
            if end not in nodes:
                dangling(t.id, f"The link “{t.trigger}”", end)
        if t.decision_group_id and t.decision_group_id not in groups:
            dangling(t.id, f"The link “{t.trigger}”", t.decision_group_id)
    for n in g.nodes:
        if tasks is not None and n.task_id and n.task_id not in tasks:
            dangling(n.id, f"The step “{n.label}”", n.task_id)
    for d in g.decision_groups:
        if d.node_id not in nodes:
            dangling(d.id, f"The decision “{d.label}”", d.node_id)
        if d.residual_transition_id and d.residual_transition_id not in transitions:
            dangling(d.id, f"The decision “{d.label}”", d.residual_transition_id)
    for r in g.risks:
        if tasks is not None and r.affected_task_id and r.affected_task_id not in tasks:
            dangling(r.id, f"The risk “{r.title}”", r.affected_task_id)
        for nid in r.affected_node_ids:
            if nid not in nodes:
                dangling(r.id, f"The risk “{r.title}”", nid)
    evidence = {e.id for e in g.evidence}
    for link in g.evidence_links:
        if link.evidence_id not in evidence:
            dangling(link.id, "An evidence link", link.evidence_id)
        if link.target_kind != "task" or tasks is not None:
            if link.target_id not in targets[link.target_kind]:
                dangling(link.id, "An evidence link", link.target_id)
    for item, label in [(a, f"The assumption “{a.text}”") for a in g.assumptions] + \
                       [(m, f"The mitigation “{m.title}”") for m in g.mitigations] + \
                       [(n, "A reviewer note") for n in g.notes]:
        kind, ref = item.target_kind, item.target_id
        if kind and ref and (kind != "task" or tasks is not None) and ref not in targets[kind]:
            dangling(item.id, label, ref)
    for m in g.mitigations:
        for change in m.estimate_changes:
            if change.transition_id not in transitions:
                dangling(m.id, f"The mitigation “{m.title}”", change.transition_id)
    return issues


# --------------------------------------------------------------------------- structure


def _structure(g: ScenarioGraph) -> list[Issue]:
    issues: list[Issue] = []
    nodes = {n.id: n for n in g.nodes if modeled(n)}
    edges = [t for t in g.transitions
             if modeled(t) and t.source_node_id in nodes and t.target_node_id in nodes]
    out: dict[str, list[Transition]] = defaultdict(list)
    for t in edges:
        out[t.source_node_id].append(t)
    label = {i: n.label for i, n in nodes.items()}

    starts = [n for n in nodes.values() if n.kind == "start"]
    goals = [n for n in nodes.values() if n.kind == "goal"]
    if not starts:
        issues.append(Issue(code="start_missing", severity="error",
                            message="The map has no start node. Generate the map or add one."))
    if len(starts) > 1:
        issues.append(Issue(code="start_multiple", severity="error",
                            message="The map has more than one start node; keep exactly one.",
                            refs=[n.id for n in starts]))
    if not goals:
        issues.append(Issue(code="goal_missing", severity="error",
                            message="The map has no goal node. Generate the map or add one."))

    if len(starts) == 1:
        reach = _reachable(starts[0].id, out)
        if goals and not any(n.id in reach for n in goals):
            issues.append(Issue(code="goal_unreachable", severity="error",
                                message="No path leads from the start to the goal.",
                                refs=[n.id for n in goals]))
        for nid in sorted(nodes.keys() - reach):
            issues.append(Issue(code="node_unreachable", severity="warning",
                                message=f"“{label[nid]}” cannot be reached from the start.",
                                refs=[nid]))

    for n in nodes.values():
        exits = out.get(n.id, [])
        if n.terminal and exits:
            issues.append(Issue(code="terminal_has_exits", severity="error",
                                message=f"“{n.label}” is an end state but has outgoing links.",
                                refs=[n.id, *(t.id for t in exits)]))
        if not n.terminal and not exits:
            issues.append(Issue(
                code="dead_end", severity="error",
                message=f"“{n.label}” has no way forward. Link it onward or mark it as an end "
                        "state.", refs=[n.id]))
        ungrouped = {t.decision_group_id for t in exits}
        if len(exits) > 1 and (None in ungrouped or len(ungrouped) > 1):
            issues.append(Issue(
                code="ungrouped_branches", severity="error",
                message=f"The {len(exits)} branches leaving “{n.label}” must belong to one "
                        "decision group before they can be analyzed.",
                refs=[n.id, *(t.id for t in exits)]))
        for t in exits:
            if t.max_traversals is not None and len(exits) == 1:
                issues.append(Issue(
                    code="retry_limit_without_fallback", severity="error",
                    message=f"“{t.trigger}” has a retry limit, but “{n.label}” has no other "
                            "branch to take when retries run out.", refs=[t.id]))

    groups = {d.id: d for d in g.decision_groups}
    for t in g.transitions:
        d = groups.get(t.decision_group_id or "")
        if d and d.node_id != t.source_node_id:
            issues.append(Issue(
                code="group_source_mismatch", severity="error",
                message=f"“{t.trigger}” is in the decision “{d.label}” but leaves a different "
                        "node.", refs=[t.id, d.id]))
    for d in g.decision_groups:
        r = next((t for t in g.transitions if t.id == d.residual_transition_id), None)
        if r and (r.decision_group_id != d.id or r.outcome != "residual"):
            issues.append(Issue(
                code="residual_invalid", severity="error",
                message=f"The remainder branch of “{d.label}” must be a residual outcome in "
                        "that decision.", refs=[d.id, r.id]))

    for comp in _cycles(nodes.keys(), out):
        inside = [t for nid in comp for t in out.get(nid, []) if t.target_node_id in comp]
        names = ", ".join(f"“{label[n]}”" for n in sorted(comp, key=label.__getitem__))
        if not any(t.max_traversals for t in inside):
            issues.append(Issue(
                code="unbounded_loop", severity="error",
                message=f"The loop through {names} has no retry limit. Set a maximum number "
                        "of attempts on one of its links.",
                refs=sorted(comp) + [t.id for t in inside]))
        leaves = any(t.target_node_id not in comp for nid in comp for t in out.get(nid, []))
        if not leaves:
            issues.append(Issue(
                code="loop_without_exit", severity="error",
                message=f"The loop through {names} has no way out to a goal or end state.",
                refs=sorted(comp)))
    return issues


def _reachable(start: str, out: dict[str, list[Transition]]) -> set[str]:
    seen, stack = {start}, [start]
    while stack:
        for t in out.get(stack.pop(), []):
            if t.target_node_id not in seen:
                seen.add(t.target_node_id)
                stack.append(t.target_node_id)
    return seen


def _cycles(nodes, out: dict[str, list[Transition]]) -> list[set[str]]:
    """Strongly connected components that contain a cycle (iterative Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[set[str]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, i = work.pop()
            if i == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            succ = [t.target_node_id for t in out.get(v, [])]
            if i < len(succ):
                work.append((v, i + 1))
                w = succ[i]
                if w not in index:
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            if low[v] == index[v]:
                comp = set()
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.add(w)
                    if w == v:
                        break
                if len(comp) > 1 or v in succ:
                    found.append(comp)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return found


def _order_conflicts(g: ScenarioGraph, plan: Plan) -> list[Issue]:
    """A prerequisite says A before B, but the modeled map only reaches A after B."""
    nodes = {n.id: n for n in g.nodes if modeled(n)}
    out: dict[str, list[Transition]] = defaultdict(list)
    for t in g.transitions:
        if modeled(t) and t.source_node_id in nodes and t.target_node_id in nodes:
            out[t.source_node_id].append(t)
    by_task = {n.task_id: n.id for n in nodes.values() if n.task_id}
    titles = {t.id: t.title for t in plan.tasks}
    issues = []
    for p in plan.prerequisites:
        a, b = by_task.get(p.before_task_id), by_task.get(p.after_task_id)
        if not a or not b or a == b:
            continue
        if a in _reachable(b, out) and b not in _reachable(a, out):
            issues.append(Issue(
                code="map_order_conflict", severity="error",
                message=f"“{titles[p.before_task_id]}” must finish before "
                        f"“{titles[p.after_task_id]}”, but the map puts it after.",
                refs=[p.id, a, b]))
    return issues


# --------------------------------------------------------------------------- readiness


class Readiness(BaseModel):
    issues: list[Issue]
    can_analyze: bool
    can_mark_reviewed: bool
    pending_suggestions: int
    blockers: list[str]


def readiness(plan: Plan, graph: ScenarioGraph) -> Readiness:
    """Whether the map can be analyzed, and whether it can be marked reviewed."""
    issues = validate_plan(plan) + validate_graph(graph, plan)
    errors = [i for i in issues if i.severity == "error"]
    # A risk's branch is decided together with the risk, so it is not counted separately.
    branch_keys = {r.dedupe_key for r in graph.risks if r.dedupe_key} | {"shared:failed"}
    pending = sum(1 for r in graph.risks if r.review_status == "suggested") + sum(
        1 for coll in (graph.nodes, graph.transitions, graph.decision_groups)
        for i in coll if i.review_status == "suggested" and i.rule_key not in branch_keys)
    blockers: list[str] = []
    if not graph.nodes:
        blockers.append("No scenario map yet. Generate one from the plan first.")
    elif errors:
        blockers.append(f"{len(errors)} problem(s) must be fixed before the map can be analyzed.")
    can_analyze = not blockers
    if pending:
        blockers.append(f"{pending} suggestion(s) still need an accept or reject decision before "
                        "the map can be marked reviewed.")
    return Readiness(issues=issues, can_analyze=can_analyze,
                     can_mark_reviewed=can_analyze and not pending,
                     pending_suggestions=pending, blockers=blockers)

