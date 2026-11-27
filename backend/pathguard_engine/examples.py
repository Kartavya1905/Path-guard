"""The two checkout examples from the brief, as plans and modeled maps.

Every number here is hypothetical and labeled so. They are used by tests and by
the demo seed (later step).

1. Checkout launch: gates 85% / 90% / 95% -> clean first-pass 72.675%.
   The checklist variant lifts scope clarity from 85% to 95% -> 81.225%
   (+8.55 percentage points). Rework and rollback branches show why eventual
   success differs from first-pass success.
2. Checkout transaction: valid cart 92%, authorized payment 90%, confirmed
   order 98% -> primary path 81.144%, with the alternate outcomes modeled.
"""

from pathguard_engine.ids import stable_id
from pathguard_engine.model import (
    DecisionGroup,
    Layout,
    Plan,
    Prerequisite,
    Project,
    ProjectContext,
    ScenarioGraph,
    ScenarioNode,
    Task,
    Transition,
)
from pathguard_engine.probability import Estimate

OWNER = "usr_demo0001"


def _hyp(percent: str, assumption: str) -> Estimate:
    return Estimate.hypothetical(percent, assumption)


class _Builder:
    def __init__(self, key: str):
        self.key = key
        self.nodes: list[ScenarioNode] = []
        self.transitions: list[Transition] = []
        self.groups: list[DecisionGroup] = []

    def node(self, name: str, kind: str, label: str, x: int, y: int, *, terminal: bool = False,
             task_id: str | None = None, severity: str | None = None) -> str:
        nid = stable_id("node", self.key, name)
        self.nodes.append(ScenarioNode(
            id=nid, kind=kind, label=label, terminal=terminal or kind == "goal",
            task_id=task_id, severity=severity, layout=Layout(x=x, y=y)))
        return nid

    def decision(self, at: str, label: str, branches: list[tuple]) -> None:
        """branches: (target, outcome, trigger, percent, assumption[, max_traversals])."""
        gid = stable_id("decision_group", self.key, at)
        self.groups.append(DecisionGroup(id=gid, node_id=at, label=label, complete=True))
        for target, outcome, trigger, percent, assumption, *limit in branches:
            self.transitions.append(Transition(
                id=stable_id("transition", self.key, at, target), source_node_id=at,
                target_node_id=target, outcome=outcome, trigger=trigger, decision_group_id=gid,
                probability=_hyp(percent, assumption),
                max_traversals=limit[0] if limit else None))

    def link(self, a: str, b: str, trigger: str, percent: str, assumption: str) -> None:
        self.transitions.append(Transition(
            id=stable_id("transition", self.key, a, b), source_node_id=a, target_node_id=b,
            outcome="success", trigger=trigger, probability=_hyp(percent, assumption)))

    def graph(self) -> ScenarioGraph:
        return ScenarioGraph(nodes=self.nodes, transitions=self.transitions,
                             decision_groups=self.groups)


def checkout_launch(variant: bool = False) -> tuple[Plan, ScenarioGraph]:
    key = "example:checkout-launch"
    pid = stable_id("project", key)
    tasks = [Task(id=stable_id("task", key, str(i)), title=t, position=i) for i, t in
             enumerate(["Confirm requirements", "Build integration", "QA and staged rollout"])]
    plan = Plan(
        project=Project(id=pid, owner_id=OWNER, title="Checkout launch (example)",
                        start="Checkout change approved",
                        goal="Reliable launch with verified checkout behavior",
                        context=ProjectContext(domain="checkout")),
        tasks=tasks,
        prerequisites=[Prerequisite(id=stable_id("prerequisite", key, str(i)),
                                    before_task_id=a.id, after_task_id=b.id)
                       for i, (a, b) in enumerate(zip(tasks, tasks[1:], strict=False))])

    b = _Builder(key)
    start = b.node("start", "start", plan.project.start, 0, 0)
    scope = b.node("scope", "step", tasks[0].title, 280, 0, task_id=tasks[0].id)
    build = b.node("build", "step", tasks[1].title, 560, 0, task_id=tasks[1].id)
    qa = b.node("qa", "step", tasks[2].title, 840, 0, task_id=tasks[2].id)
    goal = b.node("goal", "goal", plan.project.goal, 1120, 0)
    rework = b.node("rework", "failure", "Scope ambiguity found", 420, 180, severity="high")
    defect = b.node("defect", "failure", "Integration defect", 700, 180, severity="high")
    rollback = b.node("rollback", "failure", "Release defect: roll back", 980, 180,
                      severity="critical")
    blocked = b.node("blocked", "failure", "Launch blocked", 1120, 360, terminal=True,
                     severity="critical")

    scope_ok, scope_bad = ("95", "5") if variant else ("85", "15")
    scope_note = ("With the requirements checklist, scope ambiguity drops to 5%" if variant
                  else "Scope ambiguity affects about 15% of checkout changes")
    b.link(start, scope, "Work starts", "100", "Work starts once the change is approved")
    b.decision(scope, "Scope confirmed?", [
        (build, "success", "Scope is clear", scope_ok, scope_note),
        (rework, "failure", "Scope is ambiguous", scope_bad, scope_note),
    ])
    b.decision(build, "Integration works?", [
        (qa, "success", "Integration passes", "90", "Integration passes first time 90% of runs"),
        (defect, "failure", "Integration defect found", "10", "10% of builds hit a defect"),
    ])
    b.decision(qa, "Release is clean?", [
        (goal, "success", "Rollout verified", "95", "Staged rollout catches most defects"),
        (rollback, "failure", "Defect in production", "5", "5% of releases need rollback"),
    ])
    b.decision(rework, "After rework", [
        (scope, "recovery", "Clarify scope and retry", "80", "Most ambiguity is resolvable", 1),
        (blocked, "failure", "Scope can't be agreed", "20", "Some changes stall"),
    ])
    b.decision(defect, "After the defect", [
        (build, "recovery", "Fix and rebuild", "70", "Most defects are fixable", 1),
        (blocked, "failure", "Defect blocks launch", "30", "Some defects need redesign"),
    ])
    b.decision(rollback, "After rollback", [
        (qa, "recovery", "Roll back, fix and redeploy", "60", "Rollback usually works", 1),
        (blocked, "failure", "Rollback fails", "40", "Data changes can block rollback"),
    ])
    return plan, b.graph()


def checkout_transaction() -> tuple[Plan, ScenarioGraph]:
    key = "example:checkout-transaction"
    pid = stable_id("project", key)
    plan = Plan(project=Project(
        id=pid, owner_id=OWNER, title="Checkout transaction (example)",
        start="Customer starts checkout", goal="Order confirmed",
        context=ProjectContext(domain="checkout", notes="All numbers are hypothetical.")))
    b = _Builder(key)
    start = b.node("start", "start", plan.project.start, 0, 0)
    cart = b.node("cart", "step", "Validate cart", 280, 0)
    pay = b.node("pay", "step", "Authorize payment", 560, 0)
    confirm = b.node("confirm", "step", "Confirm order", 840, 0)
    goal = b.node("goal", "goal", plan.project.goal, 1120, 0)
    bad_cart = b.node("bad-cart", "failure", "Cart invalid", 420, 180, terminal=True)
    declined = b.node("declined", "failure", "Payment declined", 700, 180, terminal=True)
    unconfirmed = b.node("unconfirmed", "failure", "Order not confirmed", 980, 180,
                         terminal=True)
    b.link(start, cart, "Checkout submitted", "100", "Every checkout starts with the cart")
    b.decision(cart, "Cart valid?", [
        (pay, "success", "Cart is valid", "92", "92% of carts validate"),
        (bad_cart, "failure", "Cart is invalid", "8", "Stock or pricing problems"),
    ])
    b.decision(pay, "Payment authorized?", [
        (confirm, "success", "Payment authorized", "90", "90% of payments authorize"),
        (declined, "failure", "Payment declined", "10", "Declines and timeouts"),
    ])
    b.decision(confirm, "Order confirmed?", [
        (goal, "success", "Order confirmed", "98", "98% of paid orders confirm"),
        (unconfirmed, "failure", "Confirmation fails", "2", "Order service errors"),
    ])
    return plan, b.graph()
