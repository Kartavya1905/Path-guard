"""Deterministic rule engine: plan -> scenario map with reviewable suggestions.

The same plan always yields the same map and the same IDs (`stable_id`), so
regenerating can merge with what is already saved:

- The *spine* (start -> tasks in dependency order -> goal) restates the user's
  own plan, so it is modeled ("accepted") straight away.
- Each rule hit becomes a RiskCandidate ("suggested", no probability) and,
  where it makes sense, a suggested branch: a delay that rejoins the spine,
  or a failure with a bounded retry back to the step plus a terminal
  "goal not reached" exit.
- Regenerating refreshes rule items nobody has decided on, adds new ones,
  drops stale undecided ones, and never touches user items, edited items or
  accept/reject decisions.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.ids import stable_id
from pathguard_engine.model import (
    DecisionGroup,
    Layout,
    Plan,
    RiskCandidate,
    ScenarioGraph,
    ScenarioNode,
    Severity,
    Task,
    Transition,
)
from pathguard_engine.plan import DependencyCycleError, _edges, dependency_order, find_cycle

SPINE = "spine"
FAILED = "shared:failed"
RETRY_LIMIT = 2
X_STEP, Y_STEP = 280, 160


@dataclass(frozen=True)
class Hit:
    rule_id: str
    category: str
    title: str
    trigger: str
    rationale: str
    consequence: str
    mitigation: str
    impact: Severity
    task_id: str | None = None  # None: attaches to the start
    branch: Literal["failure", "delay"] | None = None
    branch_label: str = ""
    recovery: str = ""  # failure: retry trigger; delay: rejoin trigger
    extra: str = ""  # disambiguates several hits of one rule on one task

    @property
    def key(self) -> str:
        return ":".join(p for p in (self.rule_id, self.task_id or "project", self.extra) if p)


# --------------------------------------------------------------------------- rules


def _match(tasks: list[Task], pattern: str) -> Task | None:
    rx = re.compile(pattern, re.IGNORECASE)
    return next((t for t in tasks if rx.search(f"{t.title} {t.description or ''}")), None)


def checkout_rules(plan: Plan, order: list[Task]) -> list[Hit]:
    def pick(pattern: str, fallback: int) -> str | None:
        found = _match(order, pattern)
        if found:
            return found.id
        return order[fallback].id if order else None

    return [
        Hit("checkout.unclear_requirements", "unclear_requirements", "Unclear requirements",
            "Scope or acceptance criteria are ambiguous",
            "Checkout changes touch pricing, tax, payment and order rules, and gaps there "
            "usually surface only once building starts.",
            "Rework after build starts, which delays the launch.",
            "Agree acceptance criteria and edge cases (taxes, discounts, declined payments) "
            "with a checklist before building.",
            "high", pick(r"requirement|scope|spec|design|discover|confirm", 0),
            "failure", "Requirements need rework", "Clarify requirements and rework"),
        Hit("checkout.api_mismatch", "api_mismatch", "Payment API mismatch",
            "The integration does not match the provider's API contract",
            "Payment and order APIs change versions and behave differently in sandbox and "
            "production.",
            "Payments or order confirmations fail in testing or after release.",
            "Contract-test against the provider sandbox and pin the API version.",
            "high", pick(r"integrat|api|payment|build|implement", len(order) // 2),
            "failure", "Integration fails against the payment API",
            "Fix the integration and retest"),
        Hit("checkout.incomplete_testing", "incomplete_testing", "Incomplete testing",
            "Declines, timeouts, retries or refunds are not covered by tests",
            "Checkout failures cluster in edge cases that happy-path testing misses.",
            "Defects reach customers and block purchases.",
            "Cover declines, timeouts, retries and refunds in QA before rollout.",
            "high", pick(r"test|qa|quality|verif", -1),
            "failure", "Test gaps found late", "Close the test gaps and retest"),
        Hit("checkout.release_rollback", "release_defect", "Release defect or failed rollback",
            "A defect appears in production during rollout",
            "Checkout releases affect revenue immediately, and data changes can make rollback "
            "hard.",
            "Customers can't complete checkout until a rollback succeeds.",
            "Release behind a feature flag with a tested rollback, and watch conversion and "
            "payment errors during rollout.",
            "critical", pick(r"release|rollout|roll out|launch|deploy|go[- ]?live", -1),
            "failure", "Release defect needs rollback", "Roll back, fix and redeploy"),
    ]


def generic_rules(plan: Plan, order: list[Task], today: date | None) -> list[Hit]:
    hits: list[Hit] = []
    tasks = {t.id: t for t in plan.tasks}
    ctx = plan.project.context
    edges = _edges(plan)

    if len(plan.tasks) >= 2 and not plan.prerequisites:
        hits.append(Hit(
            "generic.missing_prerequisites", "missing_prerequisite",
            "Task order has not been confirmed", "The plan has tasks but no prerequisites",
            f"With {len(plan.tasks)} tasks and no prerequisites, the plan assumes any task can "
            "start at any time.",
            "Work may start before its inputs are ready, causing rework or idle time.",
            "Add a prerequisite wherever a task needs another task's output.", "medium"))

    cycle = find_cycle(plan)
    if cycle:
        hits.append(Hit(
            "generic.dependency_cycle", "conflicting_order", "Tasks wait on each other",
            "Prerequisites form a loop",
            "Tasks in a loop each wait for another to finish, so none of them can start: "
            + " -> ".join(tasks[t].title for t in cycle) + ".",
            "The project cannot progress past these tasks.",
            "Remove one prerequisite in the loop or split a task.", "high", cycle[0]))
    for p in plan.prerequisites:
        before, after = tasks.get(p.before_task_id), tasks.get(p.after_task_id)
        if before and after and before.id != after.id and after.position < before.position:
            hits.append(Hit(
                "generic.order_conflict", "conflicting_order",
                "Listed order conflicts with a prerequisite",
                f"“{after.title}” is listed before “{before.title}”, which must finish first",
                "The task list suggests one order and the prerequisites require another.",
                "People may start work in the listed order and then have to wait or redo it.",
                "Reorder the list to match the prerequisites, or correct the prerequisite.",
                "low", after.id, extra=p.id))

    approvals = [t for t in order if re.search(r"approv|sign[- ]?off|review|authori[sz]",
                                               f"{t.title} {t.description or ''}", re.I)]
    for t in approvals:
        hits.append(Hit(
            "generic.approval_bottleneck", "approval_bottleneck", "Approval bottleneck",
            f"“{t.title}” waits on an approval",
            "Approvals depend on other people's availability and often queue behind other work.",
            "Downstream tasks sit idle until the approval arrives.",
            "Book the approver's time in advance and agree what evidence they need.",
            "medium", t.id, "delay", f"Waiting for approval: {t.title}", "Approval received"))
    if not approvals and len(ctx.stakeholders) >= 3:
        hits.append(Hit(
            "generic.approval_bottleneck", "approval_bottleneck", "Approval bottleneck",
            f"{len(ctx.stakeholders)} stakeholders must agree",
            "Each additional stakeholder adds another review that can hold up decisions.",
            "Decisions and sign-offs slow the start of the work.",
            "Name one decision owner and agree how disagreements are settled.",
            "medium", order[0].id if order else None, "delay", "Waiting for stakeholder sign-off",
            "Sign-off received"))

    for p in plan.prerequisites:
        a, b = tasks.get(p.before_task_id), tasks.get(p.after_task_id)
        if a and b and a.owner and b.owner and a.owner.casefold() != b.owner.casefold():
            hits.append(Hit(
                "generic.handoff_delay", "handoff_delay", f"Handoff from {a.owner} to {b.owner}",
                f"“{b.title}” needs output from “{a.title}”, owned by someone else",
                "Work changing hands loses context and waits for the next owner to be free.",
                f"“{b.title}” starts late or starts with incomplete inputs.",
                "Agree the handoff contents and a date, and overlap the owners briefly.",
                "low", b.id, "delay", f"Handoff delay before {b.title}", "Handoff complete",
                extra=a.id))

    by_owner: dict[str, list[Task]] = defaultdict(list)
    for t in order:
        if t.owner:
            by_owner[t.owner.casefold()].append(t)
    reach = {t.id: _reach(t.id, edges) for t in plan.tasks}
    for owned in by_owner.values():
        for i, later in enumerate(owned):
            clash = next((e for e in owned[:i]
                          if later.id not in reach[e.id] and e.id not in reach[later.id]), None)
            if clash:
                limits = f" Resource limits: {'; '.join(ctx.resource_limits)}." \
                    if ctx.resource_limits else ""
                hits.append(Hit(
                    "generic.resource_conflict", "resource_conflict",
                    f"{later.owner} is needed on parallel tasks",
                    f"“{clash.title}” and “{later.title}” have the same owner and no order "
                    "between them",
                    "Nothing stops these tasks from running at the same time, but one person "
                    "can't do both at once." + limits,
                    f"“{later.title}” waits for {later.owner} to finish other work.",
                    "Order the tasks with a prerequisite or assign another owner.",
                    "medium", later.id, "delay", f"Waiting for {later.owner}",
                    f"{later.owner} available", extra=clash.id))

    if ctx.deadline and today:
        last = order[-1].id if order else None
        if ctx.deadline < today:
            hits.append(Hit(
                "generic.deadline_passed", "deadline_conflict", "The deadline has passed",
                f"The deadline {ctx.deadline.isoformat()} is before today",
                "The plan's deadline is already in the past.",
                "Any remaining work is late by definition.",
                "Agree a new deadline or reduce scope.", "high", last))
        else:
            needed = _critical_path(plan, edges)
            available = (ctx.deadline - today).days
            if needed is not None and needed > available:
                hits.append(Hit(
                    "generic.deadline_conflict", "deadline_conflict",
                    "The deadline is shorter than the plan",
                    f"Tasks need at least {_fmt(needed)} days but {available} remain",
                    "The longest chain of dependent tasks takes longer than the time left before "
                    "the deadline, even if everything else runs in parallel.",
                    "The goal is reached after the deadline.",
                    "Cut scope, add people to the longest chain, or move the deadline.",
                    "high", last, "delay", "Deadline missed", "Late completion"))

    if ctx.budget is not None:
        costs = [t.cost for t in plan.tasks if t.cost is not None]
        total = sum(costs, Decimal(0))
        if costs and total > ctx.budget:
            cur = f" {ctx.currency}" if ctx.currency else ""
            hits.append(Hit(
                "generic.budget_conflict", "resource_conflict", "Task costs exceed the budget",
                f"Task costs total {_fmt(total)}{cur} against a budget of {_fmt(ctx.budget)}{cur}",
                "The sum of the task cost estimates is higher than the budget.",
                "Work stops or is cut when the money runs out.",
                "Re-estimate, cut scope or secure more budget before starting.", "high"))
    return hits


def _reach(start: str, edges: dict[str, list[str]]) -> set[str]:
    seen, stack = set(), [start]
    while stack:
        for nxt in edges.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _critical_path(plan: Plan, edges: dict[str, list[str]]) -> Decimal | None:
    """Longest prerequisite chain in days, or None without durations or with a cycle."""
    if not any(t.duration_days for t in plan.tasks):
        return None
    try:
        order = dependency_order(plan)
    except DependencyCycleError:
        return None
    dur = {t.id: t.duration_days or Decimal(0) for t in plan.tasks}
    finish: dict[str, Decimal] = {}
    preds: dict[str, list[str]] = defaultdict(list)
    for a, targets in edges.items():
        for b in targets:
            preds[b].append(a)
    for t in order:
        finish[t] = max((finish[p] for p in preds[t]), default=Decimal(0)) + dur[t]
    return max(finish.values(), default=Decimal(0))


def _fmt(d: Decimal) -> str:
    return f"{d:f}".rstrip("0").rstrip(".") if "." in f"{d:f}" else f"{d:f}"


# --------------------------------------------------------------------------- build


class Generation(BaseModel):
    graph: ScenarioGraph
    added: list[str] = []
    refreshed: list[str] = []
    removed: list[str] = []
    preserved: list[str] = []  # existing decisions and edits left as they were


def build(plan: Plan, today: date | None = None) -> ScenarioGraph:
    """The fresh rule-generated map for a plan (no merging)."""
    pid = plan.project.id
    try:
        ids = dependency_order(plan)
    except DependencyCycleError:
        ids = [t.id for t in sorted(plan.tasks, key=lambda t: t.position)]
    tasks = {t.id: t for t in plan.tasks}
    order = [tasks[i] for i in ids]

    nodes: list[ScenarioNode] = []
    transitions: list[Transition] = []
    groups: list[DecisionGroup] = []
    risks: list[RiskCandidate] = []

    start = ScenarioNode(id=stable_id("node", pid, "start"), kind="start",
                         label=plan.project.start, origin="rule", rule_key=SPINE,
                         layout=Layout(x=0, y=0))
    steps = [ScenarioNode(id=stable_id("node", pid, "task", t.id), kind="step", label=t.title,
                          task_id=t.id, origin="rule", rule_key=SPINE,
                          layout=Layout(x=X_STEP * (i + 1), y=0))
             for i, t in enumerate(order)]
    goal = ScenarioNode(id=stable_id("node", pid, "goal"), kind="goal", terminal=True,
                        label=plan.project.goal, origin="rule", rule_key=SPINE,
                        layout=Layout(x=X_STEP * (len(order) + 1), y=0))
    spine = [start, *steps, goal]
    nodes += spine
    column = {n.id: i for i, n in enumerate(spine)}
    successor = {a.id: b.id for a, b in zip(spine, spine[1:], strict=False)}
    task_node = {s.task_id: s.id for s in steps}

    spine_edges: dict[str, Transition] = {}
    for a, b in zip(spine, spine[1:], strict=False):
        trigger = ("Work begins" if order else "Goal reached directly") if a.kind == "start" \
            else f"{a.label} completed"
        spine_edges[a.id] = Transition(
            id=stable_id("transition", pid, SPINE, a.id, b.id), source_node_id=a.id,
            target_node_id=b.id, outcome="success", trigger=trigger, origin="rule",
            rule_key=SPINE)

    hits: dict[str, Hit] = {}
    domain = (plan.project.context.domain or "").strip().casefold()
    for hit in (checkout_rules(plan, order) if domain == "checkout" else []) + \
            generic_rules(plan, order, today):
        hits.setdefault(hit.key, hit)  # dedupe: first hit per key wins

    failed = ScenarioNode(id=stable_id("node", pid, "failed"), kind="failure", terminal=True,
                          label="Goal not reached", severity="critical", origin="rule",
                          review_status="suggested", rule_key=FAILED,
                          layout=Layout(x=X_STEP * (len(order) + 1), y=-Y_STEP))
    branches_at: dict[str, list[str]] = defaultdict(list)  # spine node -> branch transitions

    for hit in hits.values():
        at = task_node.get(hit.task_id or "", start.id)
        affected = [at]
        if hit.branch:
            node_id = stable_id("node", pid, "branch", hit.key)
            affected.append(node_id)
            depth = len(branches_at[at]) + 1
            nodes.append(ScenarioNode(
                id=node_id, kind="delay" if hit.branch == "delay" else "failure",
                label=hit.branch_label, severity=hit.impact, origin="rule",
                review_status="suggested", rule_key=hit.key,
                layout=Layout(x=X_STEP * column[at] + X_STEP / 2, y=Y_STEP * depth)))
            enter = Transition(
                id=stable_id("transition", pid, "enter", hit.key), source_node_id=at,
                target_node_id=node_id, outcome=hit.branch, trigger=hit.trigger, origin="rule",
                review_status="suggested", rule_key=hit.key)
            transitions.append(enter)
            branches_at[at].append(enter.id)
            if hit.branch == "delay":
                transitions.append(Transition(
                    id=stable_id("transition", pid, "rejoin", hit.key), source_node_id=node_id,
                    target_node_id=successor[at], outcome="success", trigger=hit.recovery,
                    origin="rule", review_status="suggested", rule_key=hit.key))
            else:
                group_id = stable_id("decision_group", pid, "branch", hit.key)
                groups.append(DecisionGroup(
                    id=group_id, node_id=node_id, label=f"After: {hit.branch_label}",
                    origin="rule", review_status="suggested", rule_key=hit.key))
                transitions += [
                    Transition(id=stable_id("transition", pid, "retry", hit.key),
                               source_node_id=node_id, target_node_id=at, outcome="recovery",
                               trigger=hit.recovery, decision_group_id=group_id,
                               max_traversals=RETRY_LIMIT, origin="rule",
                               review_status="suggested", rule_key=hit.key),
                    Transition(id=stable_id("transition", pid, "abandon", hit.key),
                               source_node_id=node_id, target_node_id=failed.id,
                               outcome="failure", trigger="Retries exhausted or not recoverable",
                               decision_group_id=group_id, origin="rule",
                               review_status="suggested", rule_key=hit.key),
                ]
        risks.append(RiskCandidate(
            id=stable_id("risk", pid, hit.key), category=hit.category, title=hit.title,
            trigger=hit.trigger, affected_task_id=hit.task_id, affected_node_ids=affected,
            rationale=hit.rationale, consequence=hit.consequence, mitigation_hint=hit.mitigation,
            impact=hit.impact, origin="rule", rule_id=hit.rule_id, dedupe_key=hit.key))

    if any(t.target_node_id == failed.id for t in transitions):
        nodes.append(failed)

    for node_id, edge in spine_edges.items():
        if branches_at.get(node_id):
            group_id = stable_id("decision_group", pid, SPINE, node_id)
            label = next(n.label for n in spine if n.id == node_id)
            groups.append(DecisionGroup(id=group_id, node_id=node_id, label=f"Outcome of: {label}",
                                        origin="rule", rule_key=SPINE))
            spine_edges[node_id] = edge.model_copy(update={"decision_group_id": group_id})
            transitions = [t.model_copy(update={"decision_group_id": group_id})
                           if t.id in branches_at[node_id] else t for t in transitions]
    transitions = list(spine_edges.values()) + transitions
    return ScenarioGraph(nodes=nodes, transitions=transitions, decision_groups=groups,
                         risks=risks)


# --------------------------------------------------------------------------- merge


def _regenerable(item) -> bool:
    """Rule items with no human decision or edit on them."""
    if item.origin != "rule":
        return False
    return item.review_status == "suggested" or (
        item.review_status == "accepted" and getattr(item, "rule_key", None) == SPINE)


def generate(plan: Plan, existing: ScenarioGraph | None = None,
             today: date | None = None) -> Generation:
    """Build the map for `plan` and merge it into `existing`, keeping decisions."""
    fresh = build(plan, today)
    old = existing or ScenarioGraph()
    result = Generation(graph=old)
    merged: dict[str, list] = {}
    stale: dict[str, set[str]] = {}

    for field in ("nodes", "transitions", "decision_groups", "risks"):
        new_items = {i.id: i for i in getattr(fresh, field)}
        out, gone = [], set()
        for item in getattr(old, field):
            new = new_items.pop(item.id, None)
            if new is None:
                if _regenerable(item):
                    gone.add(item.id)
                out.append(item)
            elif _regenerable(item):
                keep = {"review_status": item.review_status}
                if field == "nodes":
                    keep |= {"layout": item.layout or new.layout, "collapsed": item.collapsed}
                updated = new.model_copy(update=keep)
                if updated != item:
                    result.refreshed.append(item.id)
                out.append(updated)
            else:
                result.preserved.append(item.id)
                out.append(item)
        out += new_items.values()
        result.added += new_items.keys()
        merged[field], stale[field] = out, gone

    # Drop stale undecided items unless something that stays still refers to them.
    keep_t = [t for t in merged["transitions"] if t.id not in stale["transitions"]]
    keep_r = [r for r in merged["risks"] if r.id not in stale["risks"]]
    needed_groups = {t.decision_group_id for t in keep_t}
    keep_g = [g for g in merged["decision_groups"]
              if g.id not in stale["decision_groups"] or g.id in needed_groups]
    needed_nodes = {n for t in keep_t for n in (t.source_node_id, t.target_node_id)} | \
        {g.node_id for g in keep_g} | {n for r in keep_r for n in r.affected_node_ids}
    keep_n = [n for n in merged["nodes"] if n.id not in stale["nodes"] or n.id in needed_nodes]
    kept = {i.id for i in keep_t + keep_r + keep_g + keep_n}
    result.removed = sorted(i for s in stale.values() for i in s if i not in kept)

    result.graph = old.model_copy(update={
        "nodes": keep_n, "transitions": keep_t, "decision_groups": keep_g, "risks": keep_r})
    return result


# --------------------------------------------------------------------------- review


_CONTENT_EXCLUDE = {"review_status", "layout", "collapsed"}


def mark_edits(old: ScenarioGraph, new: ScenarioGraph) -> ScenarioGraph:
    """Rule items whose content a person changed become "edited", so regenerating keeps them."""
    update = {}
    for field in ("nodes", "transitions", "decision_groups", "risks"):
        before = {i.id: i for i in getattr(old, field)}
        items = []
        for item in getattr(new, field):
            prev = before.get(item.id)
            if (prev is not None and item.origin == "rule"
                    and item.review_status in ("suggested", "accepted")
                    and item.model_dump(exclude=_CONTENT_EXCLUDE)
                    != prev.model_dump(exclude=_CONTENT_EXCLUDE)):
                item = item.model_copy(update={"review_status": "edited"})
            items.append(item)
        update[field] = items
    return new.model_copy(update=update)


def review_risk(graph: ScenarioGraph, risk_id: str, decision: Literal["accept", "reject"],
                model_branch: bool = False) -> ScenarioGraph:
    """Accept or reject a risk. Accepting with `model_branch` also models its branch."""
    risk = next((r for r in graph.risks if r.id == risk_id), None)
    if risk is None:
        raise KeyError(risk_id)
    key = risk.dedupe_key

    def own(item) -> bool:
        return key is not None and getattr(item, "rule_key", None) == key

    if decision == "accept":
        risk_status = "edited" if risk.review_status == "edited" else "accepted"
        branch = "accepted" if model_branch else None
    else:
        risk_status, branch = "rejected", "rejected"

    def apply(items):
        if branch is None:
            return items
        return [i.model_copy(update={"review_status": branch})
                if own(i) and i.review_status != branch else i for i in items]

    transitions = apply(graph.transitions)
    modeled_targets = {t.target_node_id for t in transitions
                       if t.review_status in ("accepted", "edited")}
    def shared(n: ScenarioNode) -> ScenarioNode:
        # The shared "goal not reached" end state is modeled exactly while a modeled link uses it.
        if n.rule_key != FAILED or n.review_status not in ("suggested", "accepted"):
            return n
        status = "accepted" if n.id in modeled_targets else "suggested"
        return n if n.review_status == status else n.model_copy(update={"review_status": status})

    nodes = [shared(n) for n in apply(graph.nodes)]
    risks = [r.model_copy(update={"review_status": risk_status}) if r.id == risk_id else r
             for r in graph.risks]
    return graph.model_copy(update={
        "nodes": nodes, "transitions": transitions,
        "decision_groups": apply(graph.decision_groups), "risks": risks})
