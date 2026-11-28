"""The PathGuard risk library: 100 realistic use cases PathGuard learns from.

"Learning" here means retrieval, not model training: when a project asks for
suggestions, the most similar risks that teams *accepted* in these cases are
offered (no AI needed), and they are also given to the optional local model as
examples. Rejected ideas are used as "avoid" examples for the model.

The cases are illustrative, written from common project-management practice.
They contain no statistics or probabilities; suggestions from them always start
with an unknown likelihood.
"""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from pathguard_engine.ai import NUMERIC_ESTIMATE, _nearby
from pathguard_engine.graph import modeled
from pathguard_engine.ids import stable_id
from pathguard_engine.model import (
    Plan,
    RiskCandidate,
    ScenarioGraph,
    ScenarioNode,
)
from pathguard_engine.retrieval import SourceProject, _words, select_examples

CASES_FILE = Path(__file__).with_name("cases.txt")
CATEGORIES = frozenset("""scope dependency vendor approval testing data staffing security
performance compliance communication budget schedule quality adoption logistics integration
release""".split())


class LibraryRisk(BaseModel):
    task: int  # index into the case's tasks
    category: str
    title: str
    trigger: str
    consequence: str
    control: str | None
    accepted: bool


class Case(BaseModel):
    domain: str
    title: str
    start: str
    goal: str
    tasks: list[tuple[str, str]]  # (task, owner role)
    risks: list[LibraryRisk]


class LibrarySuggestion(BaseModel):
    key: str
    case_title: str
    domain: str
    category: str
    title: str
    trigger: str
    consequence: str
    control: str | None
    score: float
    step_node_id: str | None
    step_label: str | None


def parse(text: str) -> list[Case]:
    """Parse the compact library format. Raises ValueError with the line number."""
    cases: list[Case] = []
    domain = ""
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or (line.startswith("#") and not line.startswith("##")):
            continue
        try:
            if line.startswith("## "):
                domain = line[3:].strip()
            elif line.startswith("@ "):
                title, start, goal = (p.strip() for p in line[2:].split("|"))
                cases.append(Case(domain=domain, title=title, start=start, goal=goal, tasks=[],
                                  risks=[]))
            elif line.startswith("T "):
                for part in line[2:].split(";"):
                    m = re.fullmatch(r"\s*(.+?)\s*\((.+)\)\s*", part)
                    if not m:
                        raise ValueError(f"task needs '(owner role)': {part!r}")
                    cases[-1].tasks.append((m.group(1), m.group(2)))
            elif line[:2] in ("R ", "X "):
                task, cat, title, trigger, consequence, control = (
                    p.strip() for p in line[2:].split("|"))
                cases[-1].risks.append(LibraryRisk(
                    task=int(task), category=cat, title=title, trigger=trigger,
                    consequence=consequence, control=None if control == "-" else control,
                    accepted=line[0] == "R"))
            else:
                raise ValueError("unknown line type")
        except (ValueError, IndexError) as e:
            raise ValueError(f"cases.txt line {n}: {e}") from None
    return cases


def check(cases: list[Case]) -> list[str]:
    """Problems with the library content (empty when it is valid)."""
    problems = []
    titles = [c.title for c in cases]
    for dup in {t for t in titles if titles.count(t) > 1}:
        problems.append(f"duplicate case title: {dup}")
    for c in cases:
        if not c.domain or len(c.tasks) < 2:
            problems.append(f"{c.title}: needs a domain and at least two tasks")
        if not any(r.accepted for r in c.risks):
            problems.append(f"{c.title}: needs at least one accepted risk")
        for r in c.risks:
            if not 1 <= r.task <= len(c.tasks):
                problems.append(f"{c.title}: risk '{r.title}' points to task {r.task}")
            if r.category not in CATEGORIES:
                problems.append(f"{c.title}: unknown category '{r.category}'")
            text = " ".join(filter(None, [r.title, r.trigger, r.consequence, r.control]))
            if NUMERIC_ESTIMATE.search(text):
                problems.append(f"{c.title}: risk '{r.title}' contains a numeric estimate")
    return problems


@lru_cache
def load_cases() -> tuple[Case, ...]:
    cases = parse(CASES_FILE.read_text(encoding="utf-8"))
    problems = check(cases)
    if problems:
        raise ValueError("Risk library is invalid: " + "; ".join(problems[:5]))
    return tuple(cases)


def _source(case: Case) -> SourceProject:
    nodes = [ScenarioNode(id=stable_id("node", "library", case.title, str(i)), kind="step",
                          label=task)
             for i, (task, _) in enumerate(case.tasks)]
    risks = [RiskCandidate(
        id=stable_id("risk", "library", case.title, str(i)), category=r.category, title=r.title,
        trigger=r.trigger, rationale="", consequence=r.consequence, mitigation_hint=r.control,
        affected_node_ids=[nodes[r.task - 1].id], origin="rule", rule_id=f"library:{case.title}",
        review_status="accepted" if r.accepted else "rejected")
        for i, r in enumerate(case.risks)]
    context = " ".join([case.domain, case.start, case.goal, *(t for t, _ in case.tasks)])
    return SourceProject(project_id=stable_id("project", "library", case.title),
                         title=f"Library: {case.title}", context_text=context,
                         graph=ScenarioGraph(nodes=nodes, risks=risks))


@lru_cache
def library_sources() -> tuple[SourceProject, ...]:
    return tuple(_source(c) for c in load_cases())


def _query(plan: Plan, graph: ScenarioGraph, focus_node_id: str | None) -> str:
    labels = [n.label for n in _nearby(graph, focus_node_id)] if graph.nodes else \
        [t.title for t in plan.tasks]
    p = plan.project
    return " ".join([p.context.domain or "", p.start, p.goal, *labels])


def _best_step(graph: ScenarioGraph, text: str, focus: str | None) -> ScenarioNode | None:
    """The modeled step (or start) in this map whose label best matches the risk text."""
    steps = [n for n in graph.nodes if modeled(n) and n.kind in ("step", "start")]
    if not steps:
        return None
    words = set(_words(text))
    scored = sorted(steps, key=lambda n: (-len(words & set(_words(n.label))),
                                          n.id != focus, n.kind != "step"))
    return scored[0]


def suggest(plan: Plan, graph: ScenarioGraph, focus_node_id: str | None = None,
            k: int = 5) -> list[LibrarySuggestion]:
    """The k most relevant accepted library risks for this project, not already in its map."""
    case_by_title = {f"Library: {c.title}": c for c in load_cases()}
    have = {r.title.casefold() for r in graph.risks}
    accepted, _ = select_examples(_query(plan, graph, focus_node_id), list(library_sources()),
                                  accepted=k + len(have), rejected=0)
    out: list[LibrarySuggestion] = []
    for e in accepted:
        if e.title.casefold() in have:
            continue
        case = case_by_title[e.project_title]
        risk = next(r for r in case.risks if r.title == e.title)
        step = _best_step(graph, f"{e.step} {e.title} {e.trigger}", focus_node_id)
        out.append(LibrarySuggestion(
            key=stable_id("risk", "library", case.title, e.title)[4:], case_title=case.title,
            domain=case.domain, category=risk.category, title=e.title, trigger=e.trigger,
            consequence=e.consequence, control=e.control, score=e.score,
            step_node_id=step.id if step else None, step_label=step.label if step else None))
        if len(out) >= k:
            break
    return out


def to_risk(project_id: str, s: LibrarySuggestion) -> RiskCandidate:
    """A library suggestion as a suggested risk in the normal review queue (no likelihood)."""
    return RiskCandidate(
        id=stable_id("risk", project_id, "library", s.key), category=s.category, title=s.title,
        trigger=s.trigger, rationale=f"Seen in a similar project in the PathGuard library: "
                                     f"“{s.case_title}” ({s.domain}).",
        consequence=s.consequence, mitigation_hint=s.control,
        affected_node_ids=[s.step_node_id] if s.step_node_id else [], origin="rule",
        rule_id=f"library:{s.case_title}", dedupe_key=f"library:{s.key}",
        review_status="suggested")
