"""Learn from your past reviewed risks without training a model.

When someone asks for AI suggestions, the most similar risks that people already
*accepted* (or edited) on their other projects are added to the prompt as
examples, and the most similar *rejected* ideas are listed as things to avoid.

Similarity is plain TF-IDF cosine over words, computed locally in pure Python: no
embedding model, no download, no cost, and the same input always gives the same
choice. Only text fields are used (never likelihood numbers), and examples whose
text looks like a numeric estimate are skipped so the model isn't shown numbers.
"""

import math
import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel

from pathguard_engine.ai import NUMERIC_ESTIMATE
from pathguard_engine.model import ScenarioGraph

WORD = re.compile(r"[a-z0-9]+")
STOP = frozenset("""a an and are as at be by for from has have in into is it its of on or that
the their then there this to was were will with without not no can could may might should would
our your we you they them he she i if when while after before during than so up out over""".split())


class SourceProject(BaseModel):
    """One of the asking user's other projects, as material to learn from."""

    project_id: str
    title: str
    context_text: str  # start, goal and domain
    graph: ScenarioGraph


class Example(BaseModel):
    project_id: str
    project_title: str
    step: str
    title: str
    trigger: str
    consequence: str
    control: str | None
    decision: Literal["accepted", "rejected"]
    score: float


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def _candidates(projects: list[SourceProject]) -> list[tuple[Example, str]]:
    out = []
    for p in projects:
        labels = {n.id: n.label for n in p.graph.nodes}
        for r in p.graph.risks:
            if r.review_status in ("accepted", "edited"):
                decision: Literal["accepted", "rejected"] = "accepted"
            elif r.review_status == "rejected":
                decision = "rejected"
            else:
                continue  # undecided suggestions teach nothing yet
            step = labels.get(r.affected_node_ids[0], "") if r.affected_node_ids else ""
            text = " ".join(filter(None, [r.title, r.trigger, r.consequence, r.mitigation_hint]))
            if NUMERIC_ESTIMATE.search(text):
                continue  # never show the model numbers it might copy
            ex = Example(project_id=p.project_id, project_title=p.title, step=step,
                         title=r.title, trigger=r.trigger, consequence=r.consequence,
                         control=r.mitigation_hint, decision=decision, score=0.0)
            out.append((ex, f"{p.context_text} {step} {step} {text}"))
    return out


def select_examples(query: str, projects: list[SourceProject], *, accepted: int = 4,
                    rejected: int = 3) -> tuple[list[Example], list[Example]]:
    """The most similar accepted examples and rejected ideas (score > 0), best first."""
    cands = _candidates(projects)
    if not cands or (accepted <= 0 and rejected <= 0):
        return [], []
    docs = [Counter(_words(text)) for _, text in cands]
    df = Counter(w for d in docs for w in d)
    n = len(docs)

    def vec(counts: Counter) -> dict[str, float]:
        v = {w: c * (math.log((1 + n) / (1 + df[w])) + 1) for w, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    q = vec(Counter(_words(query)))
    scored = []
    for (ex, _), d in zip(cands, docs, strict=True):
        dv = vec(d)
        score = sum(q[w] * dv.get(w, 0.0) for w in q)
        if score > 0:
            scored.append(ex.model_copy(update={"score": round(score, 4)}))
    scored.sort(key=lambda e: (-e.score, e.project_title, e.title))
    seen: set[str] = set()

    def pick(kind: str, k: int) -> list[Example]:
        chosen = []
        for e in scored:
            key = e.title.casefold()
            if e.decision == kind and key not in seen and len(chosen) < k:
                chosen.append(e)
                seen.add(key)
        return chosen

    return pick("accepted", accepted), pick("rejected", rejected)
