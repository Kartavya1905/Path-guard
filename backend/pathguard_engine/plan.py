"""Plan-level checks: prerequisite references, cycles, dependency order.

`validate_plan` never raises on bad user input; it returns issues so a draft
with problems can still be saved and the UI can explain what to fix.
"""

import heapq
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.model import Plan


class Issue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    refs: list[str] = []


class DependencyCycleError(ValueError):
    def __init__(self, cycle: list[str]):
        super().__init__("dependency cycle: " + " -> ".join(cycle))
        self.cycle = cycle


def validate_plan(plan: Plan) -> list[Issue]:
    issues: list[Issue] = []
    task_ids = [t.id for t in plan.tasks]
    known = set(task_ids)

    dup_tasks = sorted({i for i in task_ids if task_ids.count(i) > 1})
    if dup_tasks:
        issues.append(Issue(code="task_duplicate_id", severity="error",
                            message="Two tasks share the same ID.", refs=dup_tasks))

    seen_pairs: set[tuple[str, str]] = set()
    for p in plan.prerequisites:
        missing = [t for t in (p.before_task_id, p.after_task_id) if t not in known]
        if missing:
            issues.append(Issue(
                code="prerequisite_dangling", severity="error",
                message="This prerequisite points to a task that does not exist.",
                refs=[p.id, *missing]))
            continue
        if p.before_task_id == p.after_task_id:
            issues.append(Issue(
                code="prerequisite_self", severity="error",
                message="A task cannot be its own prerequisite.", refs=[p.id, p.before_task_id]))
            continue
        pair = (p.before_task_id, p.after_task_id)
        if pair in seen_pairs:
            issues.append(Issue(
                code="prerequisite_duplicate", severity="warning",
                message="This prerequisite is listed more than once.", refs=[p.id]))
        seen_pairs.add(pair)

    cycle = find_cycle(plan)
    if cycle:
        titles = {t.id: t.title for t in plan.tasks}
        issues.append(Issue(
            code="dependency_cycle", severity="error",
            message="These tasks wait on each other in a loop: "
            + " -> ".join(titles[t] for t in cycle) + ".",
            refs=cycle))
    return issues


def _edges(plan: Plan) -> dict[str, list[str]]:
    """before -> [after...] using only valid, non-self prerequisites."""
    known = {t.id for t in plan.tasks}
    out: dict[str, list[str]] = defaultdict(list)
    for p in plan.prerequisites:
        a, b = p.before_task_id, p.after_task_id
        if a in known and b in known and a != b and b not in out[a]:
            out[a].append(b)
    return out


def find_cycle(plan: Plan) -> list[str] | None:
    """One dependency cycle as [t1, t2, ..., t1], or None."""
    out = _edges(plan)
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in out.get(node, []):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state and (found := visit(nxt)):
                return found
        stack.pop()
        state[node] = 2
        return None

    for task in sorted(plan.tasks, key=lambda t: t.position):
        if task.id not in state and (found := visit(task.id)):
            return found
    return None


def dependency_order(plan: Plan) -> list[str]:
    """Task IDs in an order that respects prerequisites.

    Ties are broken by display position, so with no prerequisites this is the
    display order. Raises DependencyCycleError if no valid order exists.
    """
    out = _edges(plan)
    indegree = {t.id: 0 for t in plan.tasks}
    for targets in out.values():
        for b in targets:
            indegree[b] += 1
    pos = {t.id: (t.position, t.id) for t in plan.tasks}
    ready = [(pos[t], t) for t, d in indegree.items() if d == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _, node = heapq.heappop(ready)
        order.append(node)
        for nxt in out.get(node, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, (pos[nxt], nxt))
    if len(order) != len(plan.tasks):
        raise DependencyCycleError(find_cycle(plan) or [])
    return order
