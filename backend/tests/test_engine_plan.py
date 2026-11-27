import pytest
from pydantic import ValidationError

from pathguard_engine import Plan, Task, dependency_order, validate_plan
from pathguard_engine.plan import DependencyCycleError
from tests.engine_factories import plan, prereq, project, tasks


def codes(p: Plan) -> list[str]:
    return [i.code for i in validate_plan(p)]


# ------------------------------------------------------------ start / goal / zero tasks


def test_zero_task_project_is_valid():
    p = plan()
    assert p.tasks == []
    assert validate_plan(p) == []
    assert dependency_order(p) == []


def test_start_and_goal_are_required():
    with pytest.raises(ValidationError):
        project(start="   ")
    with pytest.raises(ValidationError):
        project(goal="")
    with pytest.raises(ValidationError):
        Plan.model_validate({"project": {"id": "prj_12345678", "owner_id": "usr_12345678",
                                         "title": "x", "start": "a"}})


def test_optional_fields_default_empty():
    p = project()
    assert p.success_criteria is None
    assert p.context.domain is None and p.context.stakeholders == []
    assert p.external_processing_opt_in is False


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        project(surprise="x")


# ------------------------------------------------------------ malformed prerequisites


def test_dangling_prerequisite_reported():
    a, = tasks("A")
    issues = validate_plan(plan([a], [prereq(a, "tsk_missing00")]))
    assert issues[0].code == "prerequisite_dangling"
    assert "tsk_missing00" in issues[0].refs


def test_self_prerequisite_reported():
    a, = tasks("A")
    assert codes(plan([a], [prereq(a, a)])) == ["prerequisite_self"]


def test_duplicate_prerequisite_is_warning():
    a, b = tasks("A", "B")
    issues = validate_plan(plan([a, b], [prereq(a, b), prereq(a, b)]))
    assert [(i.code, i.severity) for i in issues] == [("prerequisite_duplicate", "warning")]


def test_malformed_ids_rejected_by_schema():
    with pytest.raises(ValidationError):
        prereq("not-an-id", "tsk_12345678")


def test_duplicate_task_ids_reported():
    a = Task(id="tsk_aaaaaaaa", title="A", position=0)
    b = Task(id="tsk_aaaaaaaa", title="B", position=1)
    assert "task_duplicate_id" in codes(plan([a, b]))


# ------------------------------------------------------------ cycles


def test_two_task_cycle():
    a, b = tasks("A", "B")
    p = plan([a, b], [prereq(a, b), prereq(b, a)])
    issue = validate_plan(p)[0]
    assert issue.code == "dependency_cycle"
    assert issue.refs[0] == issue.refs[-1]
    assert "A -> B -> A" in issue.message
    with pytest.raises(DependencyCycleError):
        dependency_order(p)


def test_longer_cycle_only_lists_cycle_members():
    a, b, c, d = tasks("A", "B", "C", "D")
    p = plan([a, b, c, d], [prereq(a, b), prereq(b, c), prereq(c, d), prereq(d, b)])
    issue = next(i for i in validate_plan(p) if i.code == "dependency_cycle")
    assert set(issue.refs) == {b.id, c.id, d.id}


def test_invalid_draft_still_constructs():
    """Bad prerequisites are reported, not rejected, so drafts can be saved."""
    a, = tasks("A")
    p = plan([a], [prereq(a, a), prereq(a, "tsk_missing00")])
    assert len(validate_plan(p)) == 2


# ------------------------------------------------------------ display vs dependency order


def test_dependency_order_independent_of_display_order():
    qa, build, req = tasks("QA", "Build", "Requirements")  # displayed in this order
    p = plan([qa, build, req], [prereq(req, build), prereq(build, qa)])
    assert dependency_order(p) == [req.id, build.id, qa.id]
    assert [t.position for t in p.tasks] == [0, 1, 2]  # display order untouched


def test_dependency_order_ties_follow_display_order():
    a, b, c = tasks("A", "B", "C")
    assert dependency_order(plan([c, a, b])) == [a.id, b.id, c.id]


def test_dependency_order_ignores_invalid_prerequisites():
    a, b = tasks("A", "B")
    p = plan([a, b], [prereq(b, a), prereq(a, "tsk_missing00"), prereq(b, b)])
    assert dependency_order(p) == [b.id, a.id]
