"""The 100-case risk library: content checks, suggestions, quality vs random."""

from collections import Counter

import pytest

from pathguard_engine.examples import checkout_launch
from pathguard_engine.library import check, library_sources, load_cases, parse, suggest, to_risk
from pathguard_engine.library.evaluate import evaluate


def test_library_has_100_valid_cases_across_10_domains():
    cases = load_cases()
    assert len(cases) == 100
    assert Counter(c.domain for c in cases) == {d: 10 for d in {c.domain for c in cases}}
    assert len({c.domain for c in cases}) == 10
    assert check(list(cases)) == []  # includes: no numeric estimates in any text
    assert sum(r.accepted for c in cases for r in c.risks) == 300
    assert len(library_sources()) == 100


def test_parse_reports_the_bad_line():
    with pytest.raises(ValueError, match="line 3"):
        parse("## software\n@ A | start | goal\nT task without owner role\n")


def test_check_catches_numbers_and_bad_references():
    cases = parse(
        "## x\n@ A | s | g\nT one (Dev) ; two (Dev)\n"
        "R 3 | scope | Late | About 30% of projects slip | c | -\n"
    )
    problems = check(cases)
    assert any("points to task 3" in p for p in problems)
    assert any("numeric estimate" in p for p in problems)


def test_suggestions_for_a_checkout_project_come_from_checkout_cases():
    plan, graph = checkout_launch()
    found = suggest(plan, graph, None, 5)
    assert len(found) == 5
    assert found[0].domain == "checkout"
    existing = {r.title.casefold() for r in graph.risks}
    assert all(s.title.casefold() not in existing for s in found)
    assert all(s.step_node_id in {n.id for n in graph.nodes} for s in found)
    risk = to_risk(plan.project.id, found[0])
    assert risk.review_status == "suggested" and risk.likelihood.kind == "unknown"
    assert risk.rule_id.startswith("library:")


def test_library_matching_beats_random_picks():
    e = evaluate()
    for scored in (e.with_domain, e.without_domain):
        assert scored.hit_rate > e.random_baseline.hit_rate
        assert scored.precision > e.random_baseline.precision
        assert scored.recall > e.random_baseline.recall
        assert scored.same_domain > e.random_baseline.same_domain


def _project(start: str, goal: str, tasks: list[str]):
    from pathguard_engine import Plan, Project, Task, generate

    plan = Plan(
        project=Project(
            id="prj_libcheck001", owner_id="usr_demo0001", title="t", start=start, goal=goal
        ),
        tasks=[Task(id=f"tsk_libcheck{i:03d}", title=t, position=i) for i, t in enumerate(tasks)],
    )
    return plan, generate(plan, None).graph


def test_words_fold_plurals_and_skip_filler():
    from pathguard_engine.retrieval import _words

    assert _words("Set up push notifications") == ["push", "notification"]
    assert _words("New kitchen ready") == ["kitchen"]
    assert _words("Business process") == ["business", "process"]  # -ss is kept


def test_known_project_gets_close_matches_on_the_right_steps():
    plan, graph = _project(
        "Mobile app ready for beta testing",
        "App live in the App Store and Google Play",
        [
            "Build the app",
            "Beta test on real devices",
            "Set up push notifications",
            "App store submission and review",
        ],
    )
    found = {s.title: s for s in suggest(plan, graph, None, 5)}
    assert len(found) == 5 and all(s.match == "close" for s in found.values())
    assert found["Push notification setup delayed"].step_label == "Set up push notifications"
    assert found["Store review rejection"].step_label == "App store submission and review"
    assert found["Too few test devices"].step_label == "Beta test on real devices"


def test_unrelated_project_gets_only_a_few_loose_ideas():
    plan, graph = _project(
        "Kitchen renovation budget approved",
        "New kitchen installed and usable",
        [
            "Choose a contractor",
            "Order cabinets and appliances",
            "Demolition and new plumbing",
            "Install kitchen and pass inspection",
        ],
    )
    found = suggest(plan, graph, None, 5)
    assert 0 < len(found) <= 3 and all(s.match == "loose" for s in found)


def test_very_different_project_gets_nothing():
    plan, graph = _project(
        "Garden plan agreed",
        "Vegetable garden producing food",
        ["Prepare soil", "Plant seedlings", "Water and weed"],
    )
    assert suggest(plan, graph, None, 5) == []
