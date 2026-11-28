"""Optional AI suggestions (off by default).

The model is called only from this route, i.e. only when a person explicitly asks.
Graph edits, page loads and recalculations never call it. Replies are cached per
project by prompt version, provider, model and exact input, and reused. Model output
is validated strictly and becomes suggestions in the normal review queue; any
failure leaves the map unchanged and the rules-only workflow fully usable.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, NonNegativeInt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.ai import ProviderError, get_provider
from app.api import _graph_out, _load_plan, _store_graph
from app.auth import SessionDep, UserDep, project_access
from app.config import get_settings
from app.models import AiCacheRow, Member, ProjectRow, User
from app.schemas import GraphOut, In
from pathguard_engine import ScenarioGraph
from pathguard_engine.ai import (
    OUTPUT_SCHEMA,
    PROMPT_VERSION,
    _nearby,
    build_prompt,
    cache_key,
    parse,
    to_suggestions,
)
from pathguard_engine.ids import EntityId
from pathguard_engine.library import library_sources
from pathguard_engine.retrieval import SourceProject, select_examples

router = APIRouter(prefix="/api")


class AiRequest(In):
    focus_node_id: EntityId | None = None  # suggest around this step; default: the main path
    revision: NonNegativeInt


class ExampleUsed(BaseModel):
    project_title: str
    title: str
    decision: str
    score: float


class AiResult(GraphOut):
    added: list[str]
    rejected: list[str]
    cached: bool
    provider: str
    model: str
    examples_used: list[ExampleUsed] = []  # past reviewed risks shown to the model


def _sources(session: Session, user: User, current: ProjectRow, remote: bool,
             limit: int = 50) -> list[SourceProject]:
    """The asking user's own other projects, most recently updated first.

    Membership-based, so nobody else's projects are ever used. For a remote model only
    projects that opted in to external processing are included.
    """
    q = (select(ProjectRow).join(Member, Member.project_id == ProjectRow.id)
         .where(Member.user_id == user.id, ProjectRow.id != current.id)
         .order_by(ProjectRow.updated_at.desc()).limit(limit))
    if remote:
        q = q.where(ProjectRow.external_processing_opt_in.is_(True))
    out = []
    for p in session.scalars(q):
        try:
            graph = ScenarioGraph.model_validate(p.graph)
        except ValueError:
            continue
        domain = (p.context or {}).get("domain") or ""
        out.append(SourceProject(project_id=p.id, title=p.title,
                                 context_text=f"{p.start} {p.goal} {domain}", graph=graph))
    return out


@router.post("/projects/{project_id}/ai/suggestions", response_model=AiResult)
def ai_suggestions(project_id: str, body: AiRequest, session: SessionDep,
                   user: UserDep) -> AiResult:
    settings = get_settings()
    if not settings.ai_enabled:
        raise HTTPException(409, {"code": "ai_disabled",
                                  "message": "AI suggestions are switched off. The rules-based "
                                             "workflow works fully without them."})
    row, _ = project_access(session, user, project_id, "write")
    if row.revision != body.revision:
        raise HTTPException(409, {"code": "revision_conflict",
                                  "message": "This project changed in another tab or session. "
                                             "Reload before asking for suggestions.",
                                  "current_revision": row.revision})
    provider = get_provider(settings)
    if provider.remote and not (settings.external_processing_enabled
                                and row.external_processing_opt_in):
        raise HTTPException(403, {"code": "external_processing_off",
                                  "message": "The configured model runs on another machine. "
                                             "Project data is only sent there when external "
                                             "processing is enabled on the server and this "
                                             "project has opted in."})

    plan, graph = _load_plan(session, row), ScenarioGraph.model_validate(row.graph)
    learned, avoided = [], []
    if settings.ai_examples > 0 or settings.ai_avoid > 0:
        near = _nearby(graph, body.focus_node_id)
        query = " ".join([plan.project.start, plan.project.goal,
                          plan.project.context.domain or "", *(n.label for n in near)])
        pool = _sources(session, user, row, provider.remote)
        if settings.ai_use_library:
            pool += list(library_sources())  # curated examples, not user data
        learned, avoided = select_examples(
            query, pool,
            accepted=settings.ai_examples, rejected=settings.ai_avoid)
    prompt = build_prompt(
        plan, graph, body.focus_node_id, settings.ai_max_input_chars,
        learn_from=[{k: v for k, v in {"step": e.step, "title": e.title,
                                       "trigger": e.trigger, "consequence": e.consequence,
                                       "control": e.control}.items() if v}
                    for e in learned],
        avoid=[e.title for e in avoided])
    used = [ExampleUsed(project_title=e.project_title, title=e.title, decision=e.decision,
                        score=e.score)
            for e in learned[:prompt.examples_sent] + avoided[:prompt.avoid_sent]]
    key = cache_key(provider.name, provider.model, prompt)
    hit = session.get(AiCacheRow, (row.id, key))
    if hit is not None:
        raw, cached = hit.response, True
    else:
        try:
            raw = provider.complete(prompt.messages, OUTPUT_SCHEMA,
                                    settings.ai_max_output_tokens, settings.ai_timeout_seconds)
        except ProviderError as e:
            raise HTTPException(503, {"code": "ai_unavailable",
                                      "message": f"{e} Nothing was changed."}) from None
        cached = False
    try:
        batch = parse(raw)
    except ValueError as e:
        raise HTTPException(502, {"code": "ai_invalid_output",
                                  "message": f"{e} Nothing was changed."}) from None
    if not cached:
        session.add(AiCacheRow(project_id=row.id, cache_key=key, provider=provider.name,
                               model=provider.model, prompt_version=PROMPT_VERSION,
                               response=raw))

    conv = to_suggestions(batch, graph, prompt.node_ids, provider.model)
    have = {i.id for coll in (graph.nodes, graph.transitions, graph.risks) for i in coll}
    risks = [r for r in conv.risks if r.id not in have]
    added = [r.id for r in risks]
    if added:
        keys = {r.dedupe_key for r in risks}
        new = graph.model_copy(update={
            "risks": [*graph.risks, *risks],
            "nodes": [*graph.nodes, *(n for n in conv.nodes if n.rule_key in keys)],
            "transitions": [*graph.transitions,
                            *(t for t in conv.transitions if t.rule_key in keys)],
        })
        _store_graph(session, row, user, graph, new, body.revision)
        graph = new
    audit.record(session, project_id=row.id, actor_id=user.id, action="ai.suggestions",
                 target_kind="project", target_id=row.id,
                 detail={"provider": provider.name, "model": provider.model,
                         "prompt_version": PROMPT_VERSION, "cached": cached,
                         "added": len(added), "rejected": len(conv.rejected),
                         "examples": prompt.examples_sent, "avoid": prompt.avoid_sent})
    out = _graph_out(session, row, graph)
    session.commit()
    return AiResult(**out.model_dump(), added=added, rejected=conv.rejected, cached=cached,
                    provider=provider.name, model=provider.model, examples_used=used)
