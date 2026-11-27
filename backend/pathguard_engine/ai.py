"""Optional AI suggestions: prompt building and strict validation of model output.

Pure functions; no I/O (the provider call lives in app/ai.py). Rules:
- Only the relevant context is sent: start, goal, constraints, and the nodes
  near the focus (or the main path), capped in size.
- The model must return compact JSON matching `OUTPUT_SCHEMA`. Unknown fields,
  unknown node IDs and anything that looks like a numeric estimate are rejected.
- Accepted output becomes *suggestions* in the same review queue as rule
  suggestions: RiskCandidates with origin "ai", review status "suggested" and an
  unknown likelihood. Recovery suggestions also get a suggested branch with
  unknown estimates and a retry limit. Nothing is modeled until a person accepts.
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pathguard_engine.graph import modeled
from pathguard_engine.ids import stable_id
from pathguard_engine.model import (
    DecisionGroup,
    Plan,
    RiskCandidate,
    ScenarioGraph,
    ScenarioNode,
    Transition,
)

PROMPT_VERSION = "pathguard-ai-1"
MAX_NODES = 25
MAX_SUGGESTIONS = 6
# Anything that reads like a probability: "30%", "30 percent", "probability of 0.3",
# "likelihood: 0.2", "0.3 chance", "1 in 5 chance", "3 out of 10 runs".
NUMERIC_ESTIMATE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|percent\b)"
    r"|\b(?:probability|likelihood|chance|odds)\s*(?:of|is|=|:)?\s*\d"
    r"|\b\d*\.?\d+\s+(?:chance|probability|likelihood)\b"
    r"|\b\d+\s+(?:in|out of)\s+\d+\s+(?:chance|runs|cases|times|attempts)\b",
    re.IGNORECASE)

Short = Field(min_length=1, max_length=300)


class AiItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["hazard", "step", "recovery"]
    title: str = Short
    affected_node_id: str = Field(min_length=1, max_length=60)
    trigger: str = Short
    consequence: str = Field(min_length=1, max_length=600)
    rationale: str = Field(min_length=1, max_length=600)
    control: str | None = Field(default=None, max_length=600)  # suggested control / mitigation
    impact: Literal["low", "medium", "high", "critical"] | None = None
    rejoin_node_id: str | None = Field(default=None, max_length=60)  # recovery: where to retry


class AiBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[AiItem] = Field(max_length=MAX_SUGGESTIONS)


OUTPUT_SCHEMA = AiBatch.model_json_schema()

SYSTEM = " ".join([
    "You review project plans for risks. Reply with JSON only, matching the schema.",
    f"Suggest at most {MAX_SUGGESTIONS} items, each of type:",
    "hazard (a risk at a step, with a trigger, consequence and an optional control),",
    "step (a missing step that should exist), or",
    "recovery (a way to recover after a failure node; rejoin_node_id is the step to retry).",
    "Use only node IDs from the list.",
    "Never give probabilities, percentages, likelihood numbers or estimates of any kind.",
    "Do not repeat existing risks.",
])


class Prompt(BaseModel):
    messages: list[dict[str, str]]
    node_ids: list[str]
    digest: str  # identifies the input, for caching


class Conversion(BaseModel):
    risks: list[RiskCandidate]
    nodes: list[ScenarioNode]
    transitions: list[Transition]
    groups: list[DecisionGroup]
    rejected: list[str]


def _nearby(graph: ScenarioGraph, focus: str | None) -> list[ScenarioNode]:
    nodes = {n.id: n for n in graph.nodes if modeled(n)}
    if focus not in nodes:
        spine = [n for n in nodes.values() if n.kind in ("start", "step", "goal")]
        return spine[:MAX_NODES]
    links = [t for t in graph.transitions if modeled(t)]
    keep, frontier = {focus}, {focus}
    for _ in range(2):  # two hops either way
        nxt = set()
        for t in links:
            if t.source_node_id in frontier:
                nxt.add(t.target_node_id)
            if t.target_node_id in frontier:
                nxt.add(t.source_node_id)
        frontier = nxt - keep
        keep |= nxt
    return [nodes[i] for i in sorted(keep, key=list(nodes).index) if i in nodes][:MAX_NODES]


def build_prompt(plan: Plan, graph: ScenarioGraph, focus_node_id: str | None,
                 max_chars: int = 6000) -> Prompt:
    """Compact prompt with only the relevant task, constraints and nearby nodes."""
    p, ctx = plan.project, plan.project.context
    near = _nearby(graph, focus_node_id)
    constraints = {k: v for k, v in {
        "domain": ctx.domain, "deadline": str(ctx.deadline) if ctx.deadline else None,
        "budget": f"{ctx.budget} {ctx.currency or ''}".strip() if ctx.budget else None,
        "risk_tolerance": ctx.risk_tolerance, "stakeholders": ctx.stakeholders or None,
        "integrations": ctx.integrations or None, "resource_limits": ctx.resource_limits or None,
    }.items() if v}
    payload = {
        "start": p.start, "goal": p.goal, "constraints": constraints,
        "focus": focus_node_id if focus_node_id in {n.id for n in near} else None,
        "nodes": [{"id": n.id, "kind": n.kind, "label": n.label} for n in near],
        "existing_risks": [r.title for r in graph.risks if r.review_status != "rejected"][:30],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(text) > max_chars and payload["nodes"]:
        payload["nodes"] = payload["nodes"][:-1]
        payload["existing_risks"] = payload["existing_risks"][:10]
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = text[:max_chars]
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
    return Prompt(messages=messages, node_ids=[n["id"] for n in payload["nodes"]], digest=digest)


def cache_key(provider: str, model: str, prompt: Prompt) -> str:
    return hashlib.sha256(f"{PROMPT_VERSION}|{provider}|{model}|{prompt.digest}".encode()
                          ).hexdigest()


def parse(raw: str) -> AiBatch:
    """Parse model output strictly. Raises ValueError with a readable reason."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"The model's reply wasn't valid JSON ({e.msg}).") from None
    try:
        return AiBatch.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        raise ValueError(f"The model's reply didn't match the expected format ({where}: "
                         f"{first['msg']}).") from None


def to_suggestions(batch: AiBatch, graph: ScenarioGraph, allowed_node_ids: list[str],
                   model: str) -> Conversion:
    """Validated AI items -> suggested risks (and recovery branches). Never numeric."""
    nodes = {n.id: n for n in graph.nodes}
    allowed = set(allowed_node_ids)
    existing = {r.dedupe_key for r in graph.risks if r.dedupe_key} | \
        {r.title.casefold() for r in graph.risks}
    out = Conversion(risks=[], nodes=[], transitions=[], groups=[], rejected=[])
    for item in batch.suggestions:
        text = " ".join(filter(None, [item.title, item.trigger, item.consequence,
                                      item.rationale, item.control]))
        if NUMERIC_ESTIMATE.search(text):
            out.rejected.append(f"“{item.title}”: contains a numeric estimate, which AI output "
                                "may not include.")
            continue
        if item.affected_node_id not in allowed or item.affected_node_id not in nodes:
            out.rejected.append(f"“{item.title}”: refers to a step that wasn't in the request.")
            continue
        key = "ai:" + hashlib.sha256(
            f"{item.type}|{item.affected_node_id}|{item.title.casefold()}".encode()
        ).hexdigest()[:16]
        if key in existing or item.title.casefold() in existing:
            out.rejected.append(f"“{item.title}”: already suggested.")
            continue
        existing |= {key, item.title.casefold()}
        affected = [item.affected_node_id]
        hint = item.control
        if item.type == "recovery":
            at = nodes[item.affected_node_id]
            target = item.rejoin_node_id
            if at.kind != "failure" or at.terminal or target not in allowed or target not in nodes:
                out.rejected.append(f"“{item.title}”: a recovery needs a non-final failure step "
                                    "and a step to retry from the request.")
                continue
            rec = stable_id("node", key, "recovery")
            group = next((t.decision_group_id for t in graph.transitions
                          if t.source_node_id == at.id and t.decision_group_id), None)
            out.nodes.append(ScenarioNode(id=rec, kind="recovery", label=item.title,
                                          origin="ai", review_status="suggested", rule_key=key))
            out.transitions += [
                # The attempt limit sits on the way in: that link has sibling branches to
                # fall back on once the limit is reached.
                Transition(id=stable_id("transition", key, "in"), source_node_id=at.id,
                           target_node_id=rec, outcome="recovery", trigger=item.trigger,
                           decision_group_id=group, max_traversals=1, origin="ai",
                           review_status="suggested", rule_key=key),
                Transition(id=stable_id("transition", key, "retry"), source_node_id=rec,
                           target_node_id=target, outcome="recovery",
                           trigger=f"Retry: {nodes[target].label}", origin="ai",
                           review_status="suggested", rule_key=key),
            ]
            affected.append(rec)
        elif item.type == "step":
            hint = (hint + " " if hint else "") + \
                f"Add “{item.title}” as a task in the plan, then regenerate the map to place it."
        out.risks.append(RiskCandidate(
            id=stable_id("risk", key), category=f"ai_{item.type}",
            title=item.title if item.type != "step" else f"Missing step: {item.title}",
            trigger=item.trigger, affected_node_ids=affected, rationale=item.rationale,
            consequence=item.consequence, mitigation_hint=hint, impact=item.impact,
            origin="ai", rule_id=f"ai:{model}", dedupe_key=key, review_status="suggested"))
    return out
