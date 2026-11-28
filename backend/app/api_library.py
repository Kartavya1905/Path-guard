"""Risk library suggestions (no AI): similar accepted risks from 100 realistic use cases.

Listing is read-only and calculated locally. Adding a suggestion puts it into the
normal review queue as a *suggested* risk with an unknown likelihood.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import NonNegativeInt

from app.api import _graph_out, _load_plan, _store_graph
from app.auth import SessionDep, UserDep, project_access
from app.schemas import GraphOut, In
from pathguard_engine import ScenarioGraph
from pathguard_engine.ids import EntityId
from pathguard_engine.library import LibrarySuggestion, suggest, to_risk

router = APIRouter(prefix="/api")


class AddFromLibrary(In):
    key: str
    focus_node_id: EntityId | None = None
    revision: NonNegativeInt


@router.get("/projects/{project_id}/library/suggestions",
            response_model=list[LibrarySuggestion])
def library_suggestions(project_id: str, session: SessionDep, user: UserDep,
                        focus_node_id: str | None = None,
                        k: int = Query(5, ge=1, le=20)) -> list[LibrarySuggestion]:
    row, _ = project_access(session, user, project_id, "read")
    return suggest(_load_plan(session, row), ScenarioGraph.model_validate(row.graph),
                   focus_node_id or None, k)


@router.post("/projects/{project_id}/library/suggestions", response_model=GraphOut)
def add_from_library(project_id: str, body: AddFromLibrary, session: SessionDep,
                     user: UserDep) -> GraphOut:
    row, _ = project_access(session, user, project_id, "write")
    graph = ScenarioGraph.model_validate(row.graph)
    found = next((s for s in suggest(_load_plan(session, row), graph, body.focus_node_id, 20)
                  if s.key == body.key), None)
    if found is None:
        raise HTTPException(404, "That library suggestion isn't available for this project.")
    risk = to_risk(row.id, found)
    if any(r.id == risk.id for r in graph.risks):
        raise HTTPException(409, "That risk is already in this project.")
    new = graph.model_copy(update={"risks": [*graph.risks, risk]})
    _store_graph(session, row, user, graph, new, body.revision)
    out = _graph_out(session, row, new)
    session.commit()
    return out
