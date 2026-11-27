"""Audit trail: append-only events for publication, approvals and evidence."""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models import AuditEvent
from pathguard_engine import ScenarioGraph


def record(session: Session, *, project_id: str | None, actor_id: str, action: str,
           target_kind: str | None = None, target_id: str | None = None,
           detail: dict[str, Any] | None = None) -> None:
    session.add(AuditEvent(project_id=project_id, actor_id=actor_id, action=action,
                           target_kind=target_kind, target_id=target_id, detail=detail or {}))


def _by_id(items: list[BaseModel]) -> dict[str, dict[str, Any]]:
    return {i.id: i.model_dump(mode="json") for i in items}  # type: ignore[attr-defined]


def graph_changes(old: ScenarioGraph, new: ScenarioGraph) -> list[dict[str, Any]]:
    """Audit-worthy differences between two graph documents."""
    events: list[dict[str, Any]] = []

    for kind, old_items, new_items in (
        ("evidence", old.evidence, new.evidence),
        ("evidence_link", old.evidence_links, new.evidence_links),
    ):
        before, after = _by_id(old_items), _by_id(new_items)
        for i in after.keys() - before.keys():
            events.append(dict(action=f"{kind}.added", target_kind=kind, target_id=i,
                               detail={"after": after[i]}))
        for i in before.keys() - after.keys():
            events.append(dict(action=f"{kind}.removed", target_kind=kind, target_id=i,
                               detail={"before": before[i]}))
        for i in before.keys() & after.keys():
            if before[i] != after[i]:
                events.append(dict(action=f"{kind}.changed", target_kind=kind, target_id=i,
                                   detail={"before": before[i], "after": after[i]}))

    old_t, new_t = _by_id(old.transitions), _by_id(new.transitions)
    for i in old_t.keys() & new_t.keys():
        b, a = old_t[i]["probability"], new_t[i]["probability"]
        if b != a:
            action = ("estimate.reviewed" if a["verification"] == "reviewed"
                      and b["verification"] != "reviewed" else "estimate.changed")
            events.append(dict(action=action, target_kind="transition", target_id=i,
                               detail={"before": b, "after": a}))

    for kind, old_items, new_items in (
        ("risk", old.risks, new.risks),
        ("node", old.nodes, new.nodes),
        ("transition", old.transitions, new.transitions),
    ):
        before, after = _by_id(old_items), _by_id(new_items)
        for i in before.keys() & after.keys():
            if before[i]["review_status"] != after[i]["review_status"]:
                events.append(dict(action="review.decision", target_kind=kind, target_id=i,
                                   detail={"from": before[i]["review_status"],
                                           "to": after[i]["review_status"]}))
    return sorted(events, key=lambda e: (e["action"], e["target_id"]))
