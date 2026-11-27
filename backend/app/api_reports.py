"""Reports, risk-register CSV, reviewer sign-off and observed outcomes.

Exports are generated locally from stored data and permission-checked like every
other route. A report is "reviewed" only for a saved version that a reviewer has
signed off; everything else is labeled as a draft.
"""

import re
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, NonNegativeInt, PositiveInt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api import _bump_revision, _graph_out, _load_plan, _version_out, _version_row
from app.auth import SessionDep, UserDep, project_access
from app.models import OutcomeRow, ProjectRow, SignoffRow
from app.schemas import GraphOut, In, VersionSummary
from app.versions import insert_version
from pathguard_engine import (
    Analysis,
    Comparison,
    Evidence,
    EvidenceLink,
    Explorer,
    Plan,
    Readiness,
    ScenarioGraph,
    analyze,
    compare,
    explore,
    new_id,
    readiness,
)
from pathguard_engine.ids import EntityId
from pathguard_engine.model import EstimateChange, LongText, ObservedOutcome, Text
from pathguard_engine.outcomes import Proposal, apply_estimates, propose
from pathguard_engine.report import (
    EstimateSource,
    EvidenceGap,
    estimate_sources,
    evidence_gaps,
    review_summary,
    risk_register_csv,
)

router = APIRouter(prefix="/api")


class SignoffIn(In):
    note: LongText | None = None


class SignoffOut(BaseModel):
    id: str
    version_id: str
    reviewer_id: str
    reviewer_name: str
    content_hash: str
    note: str | None
    created_at: datetime


class Report(BaseModel):
    mode: Literal["draft", "reviewed"]
    status_label: str
    title: str
    generated_at: datetime
    version: VersionSummary | None
    signoffs: list[SignoffOut]
    plan: Plan
    graph: ScenarioGraph
    readiness: Readiness
    analysis: Analysis
    explorer: Explorer
    comparison: Comparison | None
    compare_version: VersionSummary | None
    estimate_sources: list[EstimateSource]
    evidence_gaps: list[EvidenceGap]
    review_summary: dict[str, dict[str, int]]


class OutcomeIn(In):
    version_id: EntityId
    decision_group_id: EntityId
    transition_id: EntityId
    observed_on: date
    period_start: date | None = None
    period_end: date | None = None
    exposure: PositiveInt
    occurrences: NonNegativeInt
    context: list[Text] = []
    notes: LongText | None = None


class OutcomeOut(ObservedOutcome):
    created_at: datetime


class ApplyIn(In):
    transition_id: EntityId
    context: list[str] = []
    sibling_changes: list[EstimateChange] = []
    evidence_quality: Literal["strong", "moderate", "weak", "anecdotal"] = "moderate"
    revision: NonNegativeInt


# --------------------------------------------------------------------------- helpers


def _summary(v) -> VersionSummary:
    return VersionSummary(id=v.id, number=v.number, label=v.label, kind=v.kind,
                          parent_version_id=v.parent_version_id, created_by=v.created_by,
                          created_at=v.created_at, content_hash=v.content_hash)


def _signoffs(session: Session, version_id: str) -> list[SignoffOut]:
    rows = session.scalars(select(SignoffRow).where(SignoffRow.version_id == version_id)
                           .order_by(SignoffRow.created_at)).all()
    return [SignoffOut.model_validate(r, from_attributes=True) for r in rows]


def _report(session: Session, row: ProjectRow, version_id: str | None,
            compare_id: str | None) -> Report:
    version = _version_out(_version_row(session, row.id, version_id)) if version_id else None
    plan, graph = (version.plan, version.graph) if version else \
        (_load_plan(session, row), ScenarioGraph.model_validate(row.graph))
    signoffs = [s for s in (_signoffs(session, version.id) if version else [])
                if s.content_hash == version.content_hash]  # type: ignore[union-attr]
    if version and signoffs:
        last = signoffs[-1]
        mode, label = "reviewed", (f"Reviewed: v{version.number} signed off by "
                                   f"{last.reviewer_name} on {last.created_at.date()}")
    elif version:
        mode, label = "draft", f"Draft: v{version.number} has no reviewer sign-off"
    else:
        mode, label = "draft", "Draft: working map, not a reviewed version"
    other = _version_out(_version_row(session, row.id, compare_id)) if compare_id else None
    return Report(
        mode=mode, status_label=label, title=plan.project.title,
        generated_at=datetime.now(UTC), version=_summary(version) if version else None,
        signoffs=signoffs, plan=plan, graph=graph, readiness=readiness(plan, graph),
        analysis=analyze(graph, plan), explorer=explore(graph),
        comparison=compare((plan, graph), (other.plan, other.graph)) if other else None,
        compare_version=_summary(other) if other else None,
        estimate_sources=estimate_sources(graph), evidence_gaps=evidence_gaps(graph),
        review_summary=review_summary(graph))


# --------------------------------------------------------------------------- reports


@router.get("/projects/{project_id}/report", response_model=Report)
def get_report(project_id: str, session: SessionDep, user: UserDep,
               version: str | None = None, compare_with: str | None = None) -> Report:
    row, _ = project_access(session, user, project_id, "read")
    return _report(session, row, version or None, compare_with or None)


@router.get("/projects/{project_id}/report/risks.csv")
def risk_register(project_id: str, session: SessionDep, user: UserDep,
                  version: str | None = None) -> Response:
    row, _ = project_access(session, user, project_id, "read")
    rep = _report(session, row, version or None, None)
    body = "﻿" + risk_register_csv(rep.graph, rep.explorer, rep.status_label)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", rep.title).strip("-").lower()[:60] or "project"
    name = f"pathguard-{slug}-{rep.mode}-risk-register.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/projects/{project_id}/versions/{version_id}/signoffs",
            response_model=list[SignoffOut])
def list_signoffs(project_id: str, version_id: str, session: SessionDep,
                  user: UserDep) -> list[SignoffOut]:
    row, _ = project_access(session, user, project_id, "read")
    _version_row(session, row.id, version_id)
    return _signoffs(session, version_id)


@router.post("/projects/{project_id}/versions/{version_id}/signoff", response_model=SignoffOut,
             status_code=201)
def sign_off(project_id: str, version_id: str, body: SignoffIn, session: SessionDep,
             user: UserDep) -> SignoffOut:
    """A reviewer signs off a saved version. Only then can its report be marked reviewed."""
    row, _ = project_access(session, user, project_id, "review")
    v = _version_out(_version_row(session, row.id, version_id))
    ready = readiness(v.plan, v.graph)
    if not ready.can_mark_reviewed:
        raise HTTPException(422, {"code": "not_reviewable",
                                  "message": " ".join(ready.blockers) or "Not reviewable yet."})
    s = SignoffRow(id=new_id("signoff"), project_id=row.id,
                   version_id=v.id, reviewer_id=user.id, reviewer_name=user.display_name,
                   content_hash=v.content_hash, note=body.note)
    session.add(s)
    session.flush()
    audit.record(session, project_id=row.id, actor_id=user.id, action="report.signed_off",
                 target_kind="version", target_id=v.id,
                 detail={"number": v.number, "content_hash": v.content_hash})
    session.commit()
    session.refresh(s)
    return SignoffOut.model_validate(s, from_attributes=True)


# --------------------------------------------------------------------------- outcomes


def _outcomes(session: Session, project_id: str) -> list[OutcomeOut]:
    rows = session.scalars(select(OutcomeRow).where(OutcomeRow.project_id == project_id)
                           .order_by(OutcomeRow.created_at)).all()
    return [OutcomeOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/projects/{project_id}/outcomes", response_model=list[OutcomeOut])
def list_outcomes(project_id: str, session: SessionDep, user: UserDep) -> list[OutcomeOut]:
    row, _ = project_access(session, user, project_id, "read")
    return _outcomes(session, row.id)


@router.post("/projects/{project_id}/outcomes", response_model=OutcomeOut, status_code=201)
def record_outcome(project_id: str, body: OutcomeIn, session: SessionDep,
                   user: UserDep) -> OutcomeOut:
    """Record what happened at one decision of a saved version."""
    row, _ = project_access(session, user, project_id, "write")
    v = _version_out(_version_row(session, row.id, body.version_id))
    t = next((x for x in v.graph.transitions if x.id == body.transition_id), None)
    if not any(g.id == body.decision_group_id for g in v.graph.decision_groups) or \
            t is None or t.decision_group_id != body.decision_group_id:
        raise HTTPException(422, {"code": "outcome_context",
                                  "message": "That branch isn't part of that decision in the "
                                             "chosen version."})
    try:
        outcome = ObservedOutcome(id=new_id("outcome"), recorded_by=user.id,
                                  **body.model_dump())
    except ValueError as e:
        raise HTTPException(422, {"code": "outcome_invalid", "message": str(e)}) from None
    saved = OutcomeRow(project_id=row.id, **outcome.model_dump())
    session.add(saved)
    session.flush()
    audit.record(session, project_id=row.id, actor_id=user.id, action="outcome.recorded",
                 target_kind="transition", target_id=t.id,
                 detail={"exposure": outcome.exposure, "occurrences": outcome.occurrences,
                         "version_id": v.id})
    session.commit()
    session.refresh(saved)
    return OutcomeOut.model_validate(saved, from_attributes=True)


@router.get("/projects/{project_id}/outcomes/proposal", response_model=Proposal)
def outcome_proposal(project_id: str, transition_id: str, session: SessionDep, user: UserDep,
                     context: str = "") -> Proposal:
    """The estimate candidate from comparable observations (nothing is applied)."""
    row, _ = project_access(session, user, project_id, "read")
    p = propose(ScenarioGraph.model_validate(row.graph), transition_id,
                list(_outcomes(session, row.id)), [c for c in context.split(",") if c.strip()])
    if p is None:
        raise HTTPException(404, "That link isn't in the working map.")
    return p


@router.post("/projects/{project_id}/outcomes/apply", response_model=GraphOut)
def apply_outcome_estimate(project_id: str, body: ApplyIn, session: SessionDep,
                           user: UserDep) -> GraphOut:
    """A reviewer approves the candidate; it is applied to the working map as a new revision.

    The map as it was is saved first as a snapshot version, so older estimates and
    evidence stay available.
    """
    row, _ = project_access(session, user, project_id, "review")
    if row.revision != body.revision:
        raise HTTPException(409, {"code": "revision_conflict",
                                  "message": "This project changed in another tab or session. "
                                             "Reload before approving.",
                                  "current_revision": row.revision})
    graph = ScenarioGraph.model_validate(row.graph)
    p = propose(graph, body.transition_id, list(_outcomes(session, row.id)), body.context)
    if p is None or p.candidate is None:
        raise HTTPException(422, {"code": "no_candidate",
                                  "message": p.message if p else "That link isn't in the map."})
    approved = p.candidate.model_copy(update={
        "verification": "reviewed", "reviewed_by": user.display_name,
        "reviewed_on": date.today()})
    if any(c.transition_id == body.transition_id for c in body.sibling_changes):
        raise HTTPException(422, "sibling_changes can't include the link being updated.")
    changes = [EstimateChange(transition_id=body.transition_id, new_probability=approved),
               *body.sibling_changes]
    new, issues = apply_estimates(graph, changes)
    if issues:
        raise HTTPException(422, {"code": "decision_invalid",
                                  "message": "; ".join(i.message for i in issues)})

    plan = _load_plan(session, row)
    insert_version(session, project_id=row.id, user_id=user.id,
                   label="Before outcome update", kind="snapshot", parent_version_id=None,
                   plan=plan, graph=graph)
    trigger = next(t.trigger for t in graph.transitions if t.id == body.transition_id)
    evidence = Evidence(
        id=new_id("evidence"), title=f"Observed outcomes: {trigger}", kind="dataset",
        source_ref="PathGuard observed outcomes", quality=body.evidence_quality,
        summary=f"{p.occurrences} of {p.exposure} eligible runs, {p.date_from} to {p.date_to}. "
                f"{p.comparability}", collected_on=p.date_to, sample_size=p.exposure)
    new = new.model_copy(update={
        "evidence": [*new.evidence, evidence],
        "evidence_links": [*new.evidence_links, EvidenceLink(
            id=new_id("evidence_link"), evidence_id=evidence.id, target_kind="transition",
            target_id=body.transition_id, note="Measured estimate approved from outcomes.")]})
    _bump_revision(session, row, body.revision)
    row.graph = new.model_dump(mode="json")
    for change in audit.graph_changes(graph, new):
        audit.record(session, project_id=row.id, actor_id=user.id, **change)
    audit.record(session, project_id=row.id, actor_id=user.id,
                 action="estimate.updated_from_outcomes", target_kind="transition",
                 target_id=body.transition_id,
                 detail={"count": p.occurrences, "denominator": p.exposure,
                         "used": p.used, "context": p.context})
    out = _graph_out(session, row, new)
    session.commit()
    return out
