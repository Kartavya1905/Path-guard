"""API request and response bodies. Domain shapes come from pathguard_engine."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, NonNegativeInt

from pathguard_engine import (
    DecisionGroup,
    Issue,
    Mitigation,
    Plan,
    Prerequisite,
    ProjectContext,
    Readiness,
    ScenarioGraph,
    ScenarioNode,
    Task,
    Transition,
)
from pathguard_engine.ids import EntityId
from pathguard_engine.model import LongText, Text


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DraftIn(In):
    """Start and goal are the only required inputs; zero tasks is valid."""

    title: Text | None = None
    start: Text
    goal: Text
    success_criteria: LongText | None = None
    context: ProjectContext = ProjectContext()
    external_processing_opt_in: bool = False
    tasks: list[Task] = []
    prerequisites: list[Prerequisite] = []


class DraftUpdate(DraftIn):
    revision: NonNegativeInt  # the revision this edit was based on


class DraftOut(BaseModel):
    plan: Plan
    issues: list[Issue]
    role: str


class ProjectSummary(BaseModel):
    id: str
    title: str
    start: str
    goal: str
    revision: int
    role: str
    updated_at: datetime


class GraphOut(BaseModel):
    graph: ScenarioGraph
    revision: int
    readiness: Readiness  # validation issues and whether the map can be analyzed or reviewed


class GenerateIn(In):
    revision: NonNegativeInt


class GenerateOut(GraphOut):
    added: list[str]
    refreshed: list[str]
    removed: list[str]
    preserved: list[str]


class ReviewIn(In):
    decision: Literal["accept", "reject"]
    model_branch: bool = False  # accept: also add the suggested branch to the modeled map
    revision: NonNegativeInt


class GraphUpdate(In):
    graph: ScenarioGraph
    revision: NonNegativeInt


class VersionCreate(In):
    label: Text
    kind: Literal["baseline", "variant", "snapshot"] = "snapshot"
    parent_version_id: EntityId | None = None
    revision: NonNegativeInt  # must match: you snapshot exactly what you saw


class VariantCreate(In):
    """A variant = its baseline plus one mitigation, applied on the server."""

    label: Text
    mitigation: Mitigation
    add_nodes: list[ScenarioNode] = []  # e.g. a safeguard or recovery step
    add_transitions: list[Transition] = []
    add_groups: list[DecisionGroup] = []


class VersionSummary(BaseModel):
    id: str
    number: int
    label: str
    kind: str
    parent_version_id: str | None
    created_by: str
    created_at: datetime
    content_hash: str


class MemberIn(In):
    user_id: EntityId
    role: Literal["editor", "reviewer", "viewer"]


class AuditOut(BaseModel):
    id: int
    actor_id: str
    action: str
    target_kind: str | None
    target_id: str | None
    detail: dict[str, Any]
    created_at: datetime


class Me(BaseModel):
    id: str
    display_name: str
    demo: bool
