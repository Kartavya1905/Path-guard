"""Saving immutable scenario versions (shared by the API and the example seed)."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.models import VersionRow
from pathguard_engine import Plan, ScenarioGraph, ScenarioVersion, new_id


def insert_version(session: Session, *, project_id: str, user_id: str, label: str, kind: str,
                   parent_version_id: str | None, plan: Plan, graph: ScenarioGraph,
                   detail: dict | None = None) -> ScenarioVersion:
    """Add the next numbered, hashed snapshot of (plan, graph). The caller commits."""
    number = (session.scalar(select(func.max(VersionRow.number))
                             .where(VersionRow.project_id == project_id)) or 0) + 1
    version = ScenarioVersion(
        id=new_id("version"), project_id=project_id, number=number, label=label, kind=kind,
        parent_version_id=parent_version_id, created_by=user_id, created_at=datetime.now(UTC),
        plan=plan, graph=graph)
    session.add(VersionRow(
        id=version.id, project_id=project_id, number=number, label=version.label,
        kind=version.kind, parent_version_id=parent_version_id, created_by=user_id,
        created_at=version.created_at,
        snapshot=version.model_dump(mode="json", include={"plan", "graph"}),
        content_hash=version.content_hash))
    session.flush()
    audit.record(session, project_id=project_id, actor_id=user_id, action="version.published",
                 target_kind="version", target_id=version.id,
                 detail={"number": number, "kind": kind, "content_hash": version.content_hash,
                         **(detail or {})})
    return version
