"""Domain schemas shared by the engine and the API.

Three relationships are kept deliberately separate:
- Prerequisite:  task A must finish before task B (planning order).
- Transition:    a possible state change between scenario nodes under a
                 trigger or condition (what could happen).
- EvidenceLink:  evidence supporting an estimate or claim. It never creates a
                 causal link between anything.

Display order (`Task.position`) is independent of dependency order
(prerequisites); see `plan.dependency_order`.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from pathguard_engine.ids import EntityId
from pathguard_engine.probability import Estimate

Text = Annotated[str, Field(min_length=1, max_length=2000)]
LongText = Annotated[str, Field(max_length=20000)]
Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Days = Annotated[Decimal, Field(ge=0, decimal_places=2)]

Origin = Literal["user", "rule", "ai"]
ReviewStatus = Literal["suggested", "accepted", "edited", "rejected"]
Severity = Literal["low", "medium", "high", "critical"]
TargetKind = Literal["node", "transition", "risk", "task", "decision_group", "mitigation"]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- plan


class ProjectContext(Schema):
    domain: str | None = None  # e.g. "checkout"; None -> generic rules only
    budget: Money | None = None
    currency: str | None = Field(default=None, max_length=3)
    deadline: date | None = None
    resource_limits: list[Text] = []
    integrations: list[Text] = []
    stakeholders: list[Text] = []
    risk_tolerance: Literal["low", "medium", "high"] | None = None
    notes: LongText | None = None


class Project(Schema):
    id: EntityId
    owner_id: EntityId
    title: Text
    start: Text  # required
    goal: Text  # required
    success_criteria: LongText | None = None
    context: ProjectContext = ProjectContext()
    revision: NonNegativeInt = 0
    # Per-project opt-in; needed on top of the global flag before any data leaves.
    external_processing_opt_in: bool = False


class Task(Schema):
    id: EntityId
    title: Text
    description: LongText | None = None
    position: NonNegativeInt  # display order only
    owner: Text | None = None
    duration_days: Days | None = None
    cost: Money | None = None


class Prerequisite(Schema):
    id: EntityId
    before_task_id: EntityId  # must finish first
    after_task_id: EntityId


class Plan(Schema):
    """Project draft: start and goal are required; zero tasks is valid."""

    project: Project
    tasks: list[Task] = []
    prerequisites: list[Prerequisite] = []


# --------------------------------------------------------------------------- graph


class Layout(Schema):
    x: float
    y: float


NodeKind = Literal["start", "step", "success", "failure", "delay", "recovery", "goal", "residual"]
TERMINAL_KINDS: frozenset[str] = frozenset({"goal", "failure", "residual"})


class ScenarioNode(Schema):
    id: EntityId
    kind: NodeKind
    label: Text
    description: LongText | None = None
    task_id: EntityId | None = None
    terminal: bool = False  # a failure node can be non-terminal if recovery leaves it
    severity: Severity | None = None
    origin: Origin = "user"
    review_status: ReviewStatus = "accepted"
    layout: Layout | None = None
    collapsed: bool = False
    rule_key: str | None = None  # set on rule-generated items; see rules.py

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind in ("goal", "residual") and not self.terminal:
            raise ValueError(f"a {self.kind} node must be terminal")
        if self.kind == "start" and self.terminal:
            raise ValueError("the start node cannot be terminal")
        return self


class Transition(Schema):
    id: EntityId
    source_node_id: EntityId
    target_node_id: EntityId
    outcome: Literal["success", "failure", "delay", "recovery", "residual"]
    trigger: Text
    condition: LongText | None = None
    decision_group_id: EntityId | None = None
    probability: Estimate = Field(default_factory=Estimate)
    # Bounds how many times a run may take this edge (retries / rework loops).
    max_traversals: PositiveInt | None = None
    cost: Money | None = None
    delay_days: Days | None = None
    origin: Origin = "user"
    review_status: ReviewStatus = "accepted"
    rule_key: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        _ai_needs_human_estimate(self.origin, self.probability)
        return self


class DecisionGroup(Schema):
    """Branches leaving one decision state; conditional probabilities sum to 100%."""

    id: EntityId
    node_id: EntityId
    label: Text
    exclusive: bool = True
    complete: bool = False  # user asserts every possible branch is modeled
    residual_transition_id: EntityId | None = None  # explicit, labeled remainder
    origin: Origin = "user"
    review_status: ReviewStatus = "accepted"
    rule_key: str | None = None


class RiskCandidate(Schema):
    id: EntityId
    category: Text  # e.g. "api_mismatch", "approval_bottleneck"
    title: Text
    trigger: Text
    affected_task_id: EntityId | None = None
    affected_node_ids: list[EntityId] = []
    rationale: LongText
    consequence: LongText
    mitigation_hint: LongText | None = None
    impact: Severity | None = None
    likelihood: Estimate = Field(default_factory=Estimate)
    origin: Origin = "user"
    rule_id: str | None = None
    dedupe_key: str | None = None
    review_status: ReviewStatus = "suggested"

    @model_validator(mode="after")
    def _check(self) -> Self:
        _ai_needs_human_estimate(self.origin, self.likelihood)
        return self


class Evidence(Schema):
    id: EntityId
    title: Text
    kind: Literal["dataset", "incident", "document", "interview", "test_result", "other"]
    source_ref: Text | None = None
    summary: LongText | None = None
    quality: Literal["strong", "moderate", "weak", "anecdotal"]
    collected_on: date | None = None
    sample_size: PositiveInt | None = None


class EvidenceLink(Schema):
    """Evidence supports an estimate or claim on the target. Not causal."""

    id: EntityId
    evidence_id: EntityId
    target_kind: TargetKind
    target_id: EntityId
    note: LongText | None = None


class Assumption(Schema):
    id: EntityId
    text: Text
    target_kind: TargetKind | None = None
    target_id: EntityId | None = None
    owner: Text | None = None
    status: Literal["open", "validated", "invalidated"] = "open"


class EstimateChange(Schema):
    transition_id: EntityId
    new_probability: Estimate


class Mitigation(Schema):
    id: EntityId
    title: Text
    target_kind: TargetKind
    target_id: EntityId
    rationale: LongText
    owner: Text | None = None
    expected_effect: LongText
    cost: Money | None = None
    delay_days: Days | None = None
    assumptions: list[Text] = []
    estimate_changes: list[EstimateChange] = []


class ReviewerNote(Schema):
    id: EntityId
    target_kind: TargetKind
    target_id: EntityId
    author_id: EntityId
    body: Text
    created_at: datetime


class ScenarioGraph(Schema):
    nodes: list[ScenarioNode] = []
    transitions: list[Transition] = []
    decision_groups: list[DecisionGroup] = []
    risks: list[RiskCandidate] = []
    evidence: list[Evidence] = []
    evidence_links: list[EvidenceLink] = []
    assumptions: list[Assumption] = []
    mitigations: list[Mitigation] = []
    notes: list[ReviewerNote] = []


# --------------------------------------------------------------------------- versions


class ScenarioVersion(Schema):
    """Immutable snapshot of a plan and its graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: EntityId
    project_id: EntityId
    number: PositiveInt
    label: Text
    kind: Literal["baseline", "variant", "snapshot"]
    parent_version_id: EntityId | None = None
    created_by: EntityId
    created_at: datetime
    plan: Plan
    graph: ScenarioGraph
    content_hash: str = ""

    @model_validator(mode="after")
    def _hash(self) -> Self:
        expected = snapshot_hash(self.plan, self.graph)
        if not self.content_hash:
            object.__setattr__(self, "content_hash", expected)
        elif self.content_hash != expected:
            raise ValueError("content_hash does not match the snapshot contents")
        return self

    def verify(self) -> bool:
        """True while the snapshot contents are unchanged since creation."""
        return snapshot_hash(self.plan, self.graph) == self.content_hash


def snapshot_hash(plan: Plan, graph: ScenarioGraph) -> str:
    payload = {"plan": plan.model_dump(mode="json"), "graph": graph.model_dump(mode="json")}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- outcomes


class ObservedOutcome(Schema):
    """What actually happened, tied to a saved version and decision context."""

    id: EntityId
    version_id: EntityId
    decision_group_id: EntityId
    transition_id: EntityId | None = None  # the branch whose occurrences are counted
    observed_on: date
    period_start: date | None = None
    period_end: date | None = None
    exposure: PositiveInt  # eligible runs that reached the decision
    occurrences: NonNegativeInt  # runs that took transition_id
    context: list[Text] = []  # tags used to judge comparability
    notes: LongText | None = None
    recorded_by: EntityId

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.occurrences > self.exposure:
            raise ValueError("occurrences cannot exceed exposure")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start must be on or before period_end")
        return self


# --------------------------------------------------------------------------- helpers


def _ai_needs_human_estimate(origin: str, estimate: Estimate) -> None:
    """AI suggestions are hypotheses: no number until a human reviews one."""
    if origin == "ai" and estimate.is_numeric and estimate.verification != "reviewed":
        raise ValueError("an AI-suggested item cannot carry an unreviewed numeric estimate")
