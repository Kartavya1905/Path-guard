"""Leave-one-out check of library suggestions.

For each case: hide it, ask the library for k suggestions using only the case's
domain (optional), start, goal and task names, and compare the suggested risks'
categories with the categories of the risks that case's team accepted.

- hit rate:  share of cases where at least one suggestion has a matching category
- precision: share of suggestions whose category matches one of the case's
- recall:    share of the case's categories covered by the suggestions
- same domain: share of suggestions taken from cases in the same domain

The random baseline picks k accepted library risks at random (fixed seed), so the
numbers show how much the similarity matching adds. This measures relevance to
the library's own examples, not real-world accuracy.
"""

import random

from pydantic import BaseModel

from pathguard_engine.library import library_sources, load_cases
from pathguard_engine.retrieval import select_examples


class Score(BaseModel):
    hit_rate: float
    precision: float
    recall: float
    same_domain: float


class Evaluation(BaseModel):
    cases: int
    k: int
    with_domain: Score
    without_domain: Score
    random_baseline: Score


def _score(rows: list[tuple[set[str], list[str], list[str], str]]) -> Score:
    hits = prec = rec = dom = 0.0
    for wanted, cats, domains, own_domain in rows:
        found = wanted & set(cats)
        hits += bool(found)
        prec += sum(c in wanted for c in cats) / len(cats) if cats else 0
        rec += len(found) / len(wanted)
        dom += sum(d == own_domain for d in domains) / len(domains) if domains else 0
    n = len(rows)
    return Score(hit_rate=round(hits / n, 3), precision=round(prec / n, 3),
                 recall=round(rec / n, 3), same_domain=round(dom / n, 3))


def evaluate(k: int = 5, seed: int = 7) -> Evaluation:
    cases = list(load_cases())
    sources = list(library_sources())
    cat_of = {(c.title, r.title): r.category for c in cases for r in c.risks}
    domain_of = {c.title: c.domain for c in cases}
    rng = random.Random(seed)
    runs: dict[str, list] = {"with": [], "without": [], "random": []}
    for case, src in zip(cases, sources, strict=True):
        wanted = {r.category for r in case.risks if r.accepted}
        pool = [s for s in sources if s.project_id != src.project_id]
        words = [case.start, case.goal, *(t for t, _ in case.tasks)]
        for name, query in (("with", " ".join([case.domain, *words])),
                            ("without", " ".join(words))):
            chosen, _ = select_examples(query, pool, accepted=k, rejected=0)
            titles = [e.project_title.removeprefix("Library: ") for e in chosen]
            runs[name].append((wanted, [cat_of[(t, e.title)] for t, e in zip(titles, chosen,
                                                                               strict=True)],
                               [domain_of[t] for t in titles], case.domain))
        others = [(c, r) for c in cases if c.title != case.title for r in c.risks if r.accepted]
        picks = rng.sample(others, k)
        runs["random"].append((wanted, [r.category for _, r in picks],
                               [c.domain for c, _ in picks], case.domain))
    return Evaluation(cases=len(cases), k=k, with_domain=_score(runs["with"]),
                      without_domain=_score(runs["without"]),
                      random_baseline=_score(runs["random"]))


if __name__ == "__main__":
    e = evaluate()
    print(f"Leave-one-out over {e.cases} cases, top {e.k} suggestions each\n")
    print("| Method | Hit rate | Precision | Recall | Same domain |")
    print("|---|---|---|---|---|")
    for name, s in (("Library matching, with domain", e.with_domain),
                    ("Library matching, no domain", e.without_domain),
                    ("Random picks (baseline)", e.random_baseline)):
        print(f"| {name} | {s.hit_rate:.0%} | {s.precision:.0%} | {s.recall:.0%} | "
              f"{s.same_domain:.0%} |")
