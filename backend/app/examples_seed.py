"""Seed the two checkout examples into a user's account (local demo user or visitor).

Every number in them is hypothetical and labeled so. Idempotent per user: an
example is only added if the user has no project with that title.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.models import Member, PrerequisiteRow, ProjectRow, TaskRow
from app.versions import insert_version
from pathguard_engine import Plan, ScenarioGraph, apply_mitigation, new_id
from pathguard_engine.examples import checklist_mitigation, checkout_launch, checkout_transaction


def _insert_project(session: Session, user_id: str, plan: Plan, graph: ScenarioGraph) -> Plan:
    pid = new_id("project")
    p = plan.project
    session.add(ProjectRow(
        id=pid, owner_id=user_id, title=p.title, start=p.start, goal=p.goal,
        success_criteria=p.success_criteria, context=p.context.model_dump(mode="json"),
        revision=1, graph=graph.model_dump(mode="json")))
    session.flush()
    session.add(Member(project_id=pid, user_id=user_id, role="owner"))
    session.add_all(TaskRow(project_id=pid, **t.model_dump()) for t in plan.tasks)
    session.flush()
    session.add_all(PrerequisiteRow(project_id=pid, **x.model_dump()) for x in plan.prerequisites)
    audit.record(session, project_id=pid, actor_id=user_id, action="project.created",
                 target_kind="project", target_id=pid, detail={"seeded_example": p.title})
    return plan.model_copy(update={"project": p.model_copy(
        update={"id": pid, "owner_id": user_id, "revision": 1})})


def seed_examples(session: Session, user_id: str) -> list[str]:
    """Add any missing example projects for `user_id`. Returns the titles added. Commits."""
    existing = set(session.scalars(select(ProjectRow.title)
                                   .where(ProjectRow.owner_id == user_id)).all())
    added: list[str] = []

    plan, graph = checkout_launch()
    if plan.project.title not in existing:
        plan = _insert_project(session, user_id, plan, graph)
        base = insert_version(session, project_id=plan.project.id, user_id=user_id,
                              label="Baseline", kind="baseline", parent_version_id=None,
                              plan=plan, graph=graph)
        vplan, vgraph, issues = apply_mitigation(plan, graph, checklist_mitigation())
        assert not issues, issues  # the shipped example must be valid
        insert_version(session, project_id=plan.project.id, user_id=user_id,
                       label="Requirements checklist", kind="variant",
                       parent_version_id=base.id, plan=vplan, graph=vgraph,
                       detail={"mitigation": checklist_mitigation().id})
        added.append(plan.project.title)

    plan, graph = checkout_transaction()
    if plan.project.title not in existing:
        plan = _insert_project(session, user_id, plan, graph)
        insert_version(session, project_id=plan.project.id, user_id=user_id, label="Baseline",
                       kind="baseline", parent_version_id=None, plan=plan, graph=graph)
        added.append(plan.project.title)
    session.commit()
    return added
