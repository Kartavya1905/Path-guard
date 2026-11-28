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
