"""Project, graph, version, member and audit routes.

Every write is one transaction: the route commits once at the end, and any
error before that rolls back every record it touched. Writes to the draft or
graph carry the `revision` they were based on; a stale revision gets 409 and
nothing is written.
"""

from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import audit
from app.auth import PERMISSIONS, SessionDep, UserDep, project_access, purge_expired_visitors
from app.config import get_settings
from app.models import (
    AuditEvent,
    Member,
    PrerequisiteRow,
    ProjectRow,
    TaskRow,
    User,
    VersionRow,
)
from app.schemas import (
    AuditOut,
    DraftIn,
    DraftOut,
    DraftUpdate,
    GenerateIn,
    GenerateOut,
    GraphOut,
    GraphUpdate,
    Me,
    MemberIn,
    ProjectSummary,
    ReviewIn,
    VersionCreate,
    VersionSummary,
)
from pathguard_engine import (
    Analysis,
    Plan,
    Prerequisite,
    Project,
    ProjectContext,
    ScenarioGraph,
    ScenarioVersion,
    Task,
    analyze,
    generate,
    mark_edits,
    new_id,
    readiness,
    review_risk,
    validate_plan,
)

router = APIRouter(prefix="/api")

UNSTORABLE = {"task_duplicate_id", "prerequisite_dangling"}


# --------------------------------------------------------------------------- helpers


def _conflict(current: int | None) -> HTTPException:
    return HTTPException(409, {
        "code": "revision_conflict",
        "message": "This project changed in another tab or session. Reload to see the latest "
                   "version before saving again.",
        "current_revision": current,
    })


def _bump_revision(session: Session, row: ProjectRow, expected: int) -> None:
    """Compare-and-swap the revision; 409 if someone saved first."""
    new = session.execute(
        update(ProjectRow)
        .where(ProjectRow.id == row.id, ProjectRow.revision == expected)
        .values(revision=ProjectRow.revision + 1, updated_at=func.now())
        .returning(ProjectRow.revision)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if new is None:
        current = session.scalar(select(ProjectRow.revision).where(ProjectRow.id == row.id))
        raise _conflict(current)
    session.refresh(row)


def _load_plan(session: Session, row: ProjectRow) -> Plan:
    tasks = session.scalars(select(TaskRow).where(TaskRow.project_id == row.id)
                            .order_by(TaskRow.position, TaskRow.id)).all()
    prereqs = session.scalars(select(PrerequisiteRow)
                              .where(PrerequisiteRow.project_id == row.id)
                              .order_by(PrerequisiteRow.id)).all()
    return Plan(
        project=Project(
            id=row.id, owner_id=row.owner_id, title=row.title, start=row.start, goal=row.goal,
            success_criteria=row.success_criteria, context=ProjectContext(**row.context),
            revision=row.revision, external_processing_opt_in=row.external_processing_opt_in),
        tasks=[Task(id=t.id, title=t.title, description=t.description, position=t.position,
                    owner=t.owner, duration_days=t.duration_days, cost=t.cost) for t in tasks],
        prerequisites=[Prerequisite(id=p.id, before_task_id=p.before_task_id,
                                    after_task_id=p.after_task_id) for p in prereqs],
    )


def _draft_out(session: Session, row: ProjectRow, role: str) -> DraftOut:
    plan = _load_plan(session, row)
    return DraftOut(plan=plan, issues=validate_plan(plan), role=role)


def _check_storable(body: DraftIn, row_id: str, owner_id: str) -> None:
    probe = Plan(project=Project(id=row_id, owner_id=owner_id, title=body.title or body.goal[:200],
                                 start=body.start, goal=body.goal),
                 tasks=body.tasks, prerequisites=body.prerequisites)
    blocking = [i for i in validate_plan(probe) if i.code in UNSTORABLE]
    if blocking:
        raise HTTPException(422, {"code": "plan_unstorable",
                                  "issues": [i.model_dump() for i in blocking]})


def _write_plan(session: Session, row: ProjectRow, body: DraftIn) -> None:
    row.title = body.title or body.goal[:200]
    row.start, row.goal = body.start, body.goal
    row.success_criteria = body.success_criteria
    row.context = body.context.model_dump(mode="json")
    row.external_processing_opt_in = body.external_processing_opt_in
    session.execute(delete(PrerequisiteRow).where(PrerequisiteRow.project_id == row.id))
    session.execute(delete(TaskRow).where(TaskRow.project_id == row.id))
    session.add_all(TaskRow(project_id=row.id, **t.model_dump()) for t in body.tasks)
    session.flush()  # tasks before prerequisites (composite foreign keys)
    session.add_all(PrerequisiteRow(project_id=row.id, **p.model_dump())
                    for p in body.prerequisites)


def _version_out(v: VersionRow) -> ScenarioVersion:
    return ScenarioVersion.model_validate({
        "id": v.id, "project_id": v.project_id, "number": v.number, "label": v.label,
        "kind": v.kind, "parent_version_id": v.parent_version_id, "created_by": v.created_by,
        "created_at": v.created_at, "content_hash": v.content_hash, **v.snapshot,
    })


# --------------------------------------------------------------------------- identity


@router.get("/me", response_model=Me)
def me(user: UserDep) -> Me:
    return Me(id=user.id, display_name=user.display_name, demo=user.is_demo)


# --------------------------------------------------------------------------- projects


@router.get("/projects", response_model=list[ProjectSummary])
def list_projects(session: SessionDep, user: UserDep) -> list[ProjectSummary]:
    rows = session.execute(
        select(ProjectRow, Member.role).join(Member, Member.project_id == ProjectRow.id)
        .where(Member.user_id == user.id).order_by(ProjectRow.updated_at.desc())
    ).all()
    return [ProjectSummary(id=p.id, title=p.title, start=p.start, goal=p.goal,
                           revision=p.revision, role=role, updated_at=p.updated_at)
            for p, role in rows]


@router.post("/projects", response_model=DraftOut, status_code=201)
def create_project(body: DraftIn, session: SessionDep, user: UserDep) -> DraftOut:
    settings = get_settings()
    if settings.visitor_identity_enabled:
        purge_expired_visitors(session, settings)
        owned = session.scalar(select(func.count()).select_from(ProjectRow)
                               .where(ProjectRow.owner_id == user.id)) or 0
        if owned >= settings.visitor_project_limit:
            raise HTTPException(429, {
                "code": "project_limit",
                "message": f"The public demo allows {settings.visitor_project_limit} projects "
                           "per browser. Delete one to create another."})
    pid = new_id("project")
    _check_storable(body, pid, user.id)
    row = ProjectRow(id=pid, owner_id=user.id, title=body.title or body.goal[:200],
                     start=body.start, goal=body.goal, revision=1, context={}, graph={})
    session.add(row)
    session.flush()
    session.add(Member(project_id=pid, user_id=user.id, role="owner"))
    session.flush()
    _write_plan(session, row, body)
    audit.record(session, project_id=pid, actor_id=user.id, action="project.created",
                 target_kind="project", target_id=pid)
    session.commit()
    session.refresh(row)
    return _draft_out(session, row, "owner")


@router.get("/projects/{project_id}", response_model=DraftOut)
def get_project(project_id: str, session: SessionDep, user: UserDep) -> DraftOut:
    row, role = project_access(session, user, project_id, "read")
    return _draft_out(session, row, role)


@router.put("/projects/{project_id}", response_model=DraftOut)
def save_project(project_id: str, body: DraftUpdate, session: SessionDep,
                 user: UserDep) -> DraftOut:
    row, role = project_access(session, user, project_id, "write")
    _check_storable(body, row.id, row.owner_id)
    _bump_revision(session, row, body.revision)
    _write_plan(session, row, body)
    session.commit()
    return _draft_out(session, row, role)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: SessionDep, user: UserDep) -> Response:
    row, _ = project_access(session, user, project_id, "admin")
    audit.record(session, project_id=row.id, actor_id=user.id, action="project.deleted",
                 target_kind="project", target_id=row.id, detail={"title": row.title})
    session.delete(row)
    session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- graph


def _graph_out(session: Session, row: ProjectRow, graph: ScenarioGraph) -> GraphOut:
    return GraphOut(graph=graph, revision=row.revision,
                    readiness=readiness(_load_plan(session, row), graph))


def _store_graph(session: Session, row: ProjectRow, user: User, old: ScenarioGraph,
                 new: ScenarioGraph, revision: int) -> None:
    _bump_revision(session, row, revision)
    row.graph = new.model_dump(mode="json")
    for change in audit.graph_changes(old, new):
        audit.record(session, project_id=row.id, actor_id=user.id, **change)


def _stamp_notes(old: ScenarioGraph, new: ScenarioGraph, user_id: str) -> ScenarioGraph:
    """Review notes are append-only: the server sets author and time; old notes can't change."""
    before = {n.id: n for n in old.notes}
    now = datetime.now(UTC)
    notes = [before.get(n.id) or n.model_copy(update={"author_id": user_id, "created_at": now})
             for n in new.notes]
    kept = {n.id for n in notes}
    notes += [n for n in old.notes if n.id not in kept]
    return new.model_copy(update={"notes": notes})


@router.get("/projects/{project_id}/graph", response_model=GraphOut)
def get_graph(project_id: str, session: SessionDep, user: UserDep) -> GraphOut:
    row, _ = project_access(session, user, project_id, "read")
    return _graph_out(session, row, ScenarioGraph.model_validate(row.graph))


@router.put("/projects/{project_id}/graph", response_model=GraphOut)
def save_graph(project_id: str, body: GraphUpdate, session: SessionDep,
               user: UserDep) -> GraphOut:
    """Save the whole map. Incomplete maps save; `readiness` says what blocks analysis."""
    row, _ = project_access(session, user, project_id, "write")
    old = ScenarioGraph.model_validate(row.graph)
    # Changed rule items become "edited" so they survive regeneration.
    new = _stamp_notes(old, mark_edits(old, body.graph), user.id)
    _store_graph(session, row, user, old, new, body.revision)
    out = _graph_out(session, row, new)
    session.commit()
    return out


@router.post("/projects/{project_id}/graph/generate", response_model=GenerateOut)
def generate_graph(project_id: str, body: GenerateIn, session: SessionDep,
                   user: UserDep) -> GenerateOut:
    """Run the rule engine on the saved plan and merge into the saved map."""
    row, _ = project_access(session, user, project_id, "write")
    old = ScenarioGraph.model_validate(row.graph)
    result = generate(_load_plan(session, row), old, today=date.today())
    _store_graph(session, row, user, old, result.graph, body.revision)
    audit.record(session, project_id=row.id, actor_id=user.id, action="graph.generated",
                 target_kind="project", target_id=row.id,
                 detail={k: len(getattr(result, k))
                         for k in ("added", "refreshed", "removed", "preserved")})
    out = _graph_out(session, row, result.graph)
    session.commit()
    return GenerateOut(**out.model_dump(), added=result.added, refreshed=result.refreshed,
                       removed=result.removed, preserved=result.preserved)


@router.post("/projects/{project_id}/risks/{risk_id}/review", response_model=GraphOut)
def review_suggestion(project_id: str, risk_id: str, body: ReviewIn, session: SessionDep,
                      user: UserDep) -> GraphOut:
    """Accept or reject a suggested risk; editors and reviewers may decide."""
    row, role = project_access(session, user, project_id, "read")
    if not PERMISSIONS[role] & {"write", "review"}:
        raise HTTPException(403, "Your role on this project cannot review suggestions.")
    old = ScenarioGraph.model_validate(row.graph)
    try:
        new = review_risk(old, risk_id, body.decision, body.model_branch)
    except KeyError:
        raise HTTPException(404, "No such risk in this project's map.") from None
    _store_graph(session, row, user, old, new, body.revision)
    out = _graph_out(session, row, new)
    session.commit()
    return out


@router.get("/projects/{project_id}/analysis", response_model=Analysis)
def analyze_graph(project_id: str, session: SessionDep, user: UserDep,
                  max_depth: int = Query(200, ge=1, le=1000),
                  max_expansions: int = Query(20000, ge=1, le=200000),
                  max_results: int = Query(10, ge=1, le=100)) -> Analysis:
    """Probability analysis of the saved map. Local calculation only; nothing is stored."""
    row, _ = project_access(session, user, project_id, "read")
    return analyze(ScenarioGraph.model_validate(row.graph), _load_plan(session, row),
                   max_depth=max_depth, max_expansions=max_expansions, max_results=max_results)


# --------------------------------------------------------------------------- versions


@router.get("/projects/{project_id}/versions", response_model=list[VersionSummary])
def list_versions(project_id: str, session: SessionDep, user: UserDep) -> list[VersionSummary]:
    project_access(session, user, project_id, "read")
    rows = session.scalars(select(VersionRow).where(VersionRow.project_id == project_id)
                           .order_by(VersionRow.number)).all()
    return [VersionSummary.model_validate(r, from_attributes=True) for r in rows]


@router.post("/projects/{project_id}/versions", response_model=ScenarioVersion, status_code=201)
def save_version(project_id: str, body: VersionCreate, session: SessionDep,
                 user: UserDep) -> ScenarioVersion:
    row, _ = project_access(session, user, project_id, "publish", lock=True)
    if row.revision != body.revision:
        raise _conflict(row.revision)
    if body.parent_version_id:
        parent = session.get(VersionRow, body.parent_version_id)
        if parent is None or parent.project_id != row.id:
            raise HTTPException(422, "parent_version_id is not a version of this project.")
    elif body.kind == "variant":
        raise HTTPException(422, "A variant needs parent_version_id (its baseline).")

    number = (session.scalar(select(func.max(VersionRow.number))
                             .where(VersionRow.project_id == row.id)) or 0) + 1
    version = ScenarioVersion(
        id=new_id("version"), project_id=row.id, number=number, label=body.label,
        kind=body.kind, parent_version_id=body.parent_version_id, created_by=user.id,
        created_at=datetime.now(UTC), plan=_load_plan(session, row),
        graph=ScenarioGraph.model_validate(row.graph))
    snapshot = version.model_dump(mode="json", include={"plan", "graph"})
    session.add(VersionRow(
        id=version.id, project_id=row.id, number=number, label=version.label,
        kind=version.kind, parent_version_id=version.parent_version_id, created_by=user.id,
        created_at=version.created_at, snapshot=snapshot, content_hash=version.content_hash))
    audit.record(session, project_id=row.id, actor_id=user.id, action="version.published",
                 target_kind="version", target_id=version.id,
                 detail={"number": number, "kind": version.kind,
                         "content_hash": version.content_hash})
    session.commit()
    return version


@router.get("/projects/{project_id}/versions/{version_id}", response_model=ScenarioVersion)
def get_version(project_id: str, version_id: str, session: SessionDep,
                user: UserDep) -> ScenarioVersion:
    project_access(session, user, project_id, "read")
    v = session.get(VersionRow, version_id)
    if v is None or v.project_id != project_id:
        raise HTTPException(404, "Version not found.")
    return _version_out(v)


# --------------------------------------------------------------------------- members, audit


@router.post("/projects/{project_id}/members", status_code=204)
def add_member(project_id: str, body: MemberIn, session: SessionDep, user: UserDep) -> Response:
    row, _ = project_access(session, user, project_id, "admin")
    if session.get(User, body.user_id) is None:
        raise HTTPException(422, "Unknown user.")
    existing = session.get(Member, (row.id, body.user_id))
    if existing and existing.role == "owner":
        raise HTTPException(422, "The owner's role cannot be changed here.")
    if existing:
        existing.role = body.role
    else:
        session.add(Member(project_id=row.id, user_id=body.user_id, role=body.role))
    audit.record(session, project_id=row.id, actor_id=user.id, action="member.set",
                 target_kind="user", target_id=body.user_id, detail={"role": body.role})
    session.commit()
    return Response(status_code=204)


@router.get("/projects/{project_id}/audit", response_model=list[AuditOut])
def list_audit(project_id: str, session: SessionDep, user: UserDep) -> list[AuditOut]:
    project_access(session, user, project_id, "read")
    rows = session.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id)
                           .order_by(AuditEvent.id)).all()
    return [AuditOut.model_validate(r, from_attributes=True) for r in rows]
