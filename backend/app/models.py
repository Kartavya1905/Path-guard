"""SQLAlchemy tables. Keep in step with migrations/versions.

Storage choices:
- Project, tasks and prerequisites are relational rows (prerequisites have
  foreign keys to tasks, so a prerequisite can never dangle in storage).
- The scenario graph (nodes, transitions, decision groups, risks, evidence,
  evidence links, assumptions, mitigations, notes, layout) is one JSONB
  document validated by `pathguard_engine.ScenarioGraph` on every write, and
  saved atomically with the project revision.
- Scenario versions are insert-only; a database trigger rejects UPDATE.
- Audit events are append-only; a trigger rejects UPDATE and DELETE.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

ID = String(48)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class ProjectRow(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(2000))
    start: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    success_criteria: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    external_processing_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class Member(Base):
    __tablename__ = "project_members"
    project_id: Mapped[str] = mapped_column(
        ID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | editor | reviewer | viewer


class TaskRow(Base):
    __tablename__ = "tasks"
    project_id: Mapped[str] = mapped_column(
        ID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    title: Mapped[str] = mapped_column(String(2000))
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    owner: Mapped[str | None] = mapped_column(String(2000))
    duration_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class PrerequisiteRow(Base):
    __tablename__ = "prerequisites"
    __table_args__ = (
        ForeignKeyConstraint(["project_id", "before_task_id"], ["tasks.project_id", "tasks.id"],
                             ondelete="CASCADE"),
        ForeignKeyConstraint(["project_id", "after_task_id"], ["tasks.project_id", "tasks.id"],
                             ondelete="CASCADE"),
    )
    project_id: Mapped[str] = mapped_column(
        ID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    before_task_id: Mapped[str] = mapped_column(ID)
    after_task_id: Mapped[str] = mapped_column(ID)


class VersionRow(Base):
    __tablename__ = "scenario_versions"
    __table_args__ = (UniqueConstraint("project_id", "number"),)
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ID, ForeignKey("projects.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(2000))
    kind: Mapped[str] = mapped_column(String(16))
    parent_version_id: Mapped[str | None] = mapped_column(ID, ForeignKey("scenario_versions.id"))
    created_by: Mapped[str] = mapped_column(ID, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"plan": ..., "graph": ...}
    content_hash: Mapped[str] = mapped_column(String(64))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ID, index=True)  # no FK: outlives projects
    actor_id: Mapped[str] = mapped_column(ID)
    action: Mapped[str] = mapped_column(String(64))
    target_kind: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(ID)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _now()


class SignoffRow(Base):
    """A reviewer's sign-off on one saved version. Insert-only."""

    __tablename__ = "report_signoffs"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ID, ForeignKey("projects.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(
        ID, ForeignKey("scenario_versions.id", ondelete="CASCADE"))
    reviewer_id: Mapped[str] = mapped_column(ID)
    reviewer_name: Mapped[str] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class OutcomeRow(Base):
    """What actually happened at a decision, tied to a saved version. Insert-only."""

    __tablename__ = "observed_outcomes"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    project_id: Mapped[str] = mapped_column(ID, ForeignKey("projects.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(
        ID, ForeignKey("scenario_versions.id", ondelete="CASCADE"))
    decision_group_id: Mapped[str] = mapped_column(ID)
    transition_id: Mapped[str | None] = mapped_column(ID)
    observed_on: Mapped[date] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    exposure: Mapped[int] = mapped_column(Integer)
    occurrences: Mapped[int] = mapped_column(Integer)
    context: Mapped[list[str]] = mapped_column(JSONB, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = _now()


class AiCacheRow(Base):
    """A stored model reply for one exact AI request (prompt version, provider, model, input)."""

    __tablename__ = "ai_suggestion_cache"
    project_id: Mapped[str] = mapped_column(
        ID, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    response: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
