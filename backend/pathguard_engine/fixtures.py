"""Synthetic maps for performance checks (not user data, not seeded).

`branching_map(steps, retries)` builds start -> N steps -> goal where every step
has a complete 3-way decision (success / failure with retry / delay that
rejoins), and every failure node retries its step up to `retries` times. The
number of distinct run states grows quickly with N and `retries`, which is
what the analysis limits are for.
"""

from pathguard_engine.ids import stable_id
from pathguard_engine.model import DecisionGroup, Layout, ScenarioGraph, ScenarioNode, Transition
from pathguard_engine.probability import Estimate


def _p(percent: str) -> Estimate:
    return Estimate.hypothetical(percent, "synthetic fixture")


def branching_map(steps: int, retries: int = 2) -> ScenarioGraph:
    key = f"fixture:{steps}:{retries}"

    def nid(name: str) -> str:
        return stable_id("node", key, name)

    nodes = [ScenarioNode(id=nid("start"), kind="start", label="Start", layout=Layout(x=0, y=0))]
    nodes += [ScenarioNode(id=nid(f"s{i}"), kind="step", label=f"Step {i + 1}",
                           layout=Layout(x=280 * (i + 1), y=0)) for i in range(steps)]
    nodes.append(ScenarioNode(id=nid("goal"), kind="goal", terminal=True, label="Goal",
                              layout=Layout(x=280 * (steps + 1), y=0)))
    nodes.append(ScenarioNode(id=nid("fail"), kind="failure", terminal=True, label="Failed",
                              layout=Layout(x=280 * (steps + 1), y=320)))
    transitions = [Transition(id=stable_id("transition", key, "go"), source_node_id=nid("start"),
                              target_node_id=nid("s0"), outcome="success", trigger="Go",
                              probability=_p("100"))]
    groups = []
    for i in range(steps):
        here, nxt = nid(f"s{i}"), nid(f"s{i + 1}") if i + 1 < steps else nid("goal")
        f, d = nid(f"f{i}"), nid(f"d{i}")
        nodes += [ScenarioNode(id=f, kind="failure", label=f"Step {i + 1} fails",
                               layout=Layout(x=280 * (i + 1) + 140, y=160)),
                  ScenarioNode(id=d, kind="delay", label=f"Step {i + 1} delayed",
                               layout=Layout(x=280 * (i + 1) + 140, y=-160))]
        g1 = stable_id("decision_group", key, here)
        g2 = stable_id("decision_group", key, f)
        groups += [DecisionGroup(id=g1, node_id=here, label=f"Step {i + 1}", complete=True),
                   DecisionGroup(id=g2, node_id=f, label=f"After step {i + 1} fails",
                                 complete=True)]
        transitions += [
            Transition(id=stable_id("transition", key, here, "ok"), source_node_id=here,
                       target_node_id=nxt, outcome="success", trigger="Done",
                       decision_group_id=g1, probability=_p("80")),
            Transition(id=stable_id("transition", key, here, "fail"), source_node_id=here,
                       target_node_id=f, outcome="failure", trigger="Fails",
                       decision_group_id=g1, probability=_p("10")),
            Transition(id=stable_id("transition", key, here, "delay"), source_node_id=here,
                       target_node_id=d, outcome="delay", trigger="Slips",
                       decision_group_id=g1, probability=_p("10")),
            Transition(id=stable_id("transition", key, d, "rejoin"), source_node_id=d,
                       target_node_id=nxt, outcome="success", trigger="Catches up",
                       probability=_p("100")),
            Transition(id=stable_id("transition", key, f, "retry"), source_node_id=f,
                       target_node_id=here, outcome="recovery", trigger="Retry",
                       decision_group_id=g2, max_traversals=retries, probability=_p("70")),
            Transition(id=stable_id("transition", key, f, "stop"), source_node_id=f,
                       target_node_id=nid("fail"), outcome="failure", trigger="Give up",
                       decision_group_id=g2, probability=_p("30")),
        ]
    return ScenarioGraph(nodes=nodes, transitions=transitions, decision_groups=groups)
