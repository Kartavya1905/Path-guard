from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pathguard_engine import (
    Estimate,
    ObservedOutcome,
    RiskCandidate,
    ScenarioGraph,
    ScenarioNode,
    ScenarioVersion,
    Transition,
    from_percent,
    new_id,
    stable_id,
    to_percent,
)
from pathguard_engine.probability import ExpertSource, MeasuredSource
from tests.engine_factories import OWNER, plan, tasks

# ------------------------------------------------------------ ids


def test_ids_have_prefix_and_are_unique():
    a, b = new_id("task"), new_id("task")
    assert a.startswith("tsk_") and a != b


def test_stable_ids_are_deterministic():
    assert stable_id("risk", "api_mismatch", "tsk_1") == stable_id("risk", "api_mismatch", "tsk_1")
    assert stable_id("risk", "api_mismatch", "tsk_1") != stable_id("risk", "api_mismatch", "tsk_2")


# ------------------------------------------------------------ estimates


def test_percent_conversion_is_exact():
    assert from_percent("72.675") == Decimal("0.72675")
    assert to_percent(Decimal("0.85")) == Decimal("85.00")
    with pytest.raises(TypeError):
        from_percent(85.0)


def test_unknown_is_default_and_has_no_numbers():
    e = Estimate()
    assert e.kind == "unknown" and not e.is_numeric
    with pytest.raises(ValidationError):
        Estimate(kind="unknown", value=Decimal("0.5"))


def test_point_needs_value_and_source():
    with pytest.raises(ValidationError):
        Estimate(kind="point")
    with pytest.raises(ValidationError):  # numeric but source unknown
        Estimate(kind="point", value=Decimal("0.5"))
    e = Estimate.hypothetical("85", "Seeded example")
    assert e.value == Decimal("0.85") and e.source.label == "Hypothetical"


@pytest.mark.parametrize("bad", ["1.01", "-0.01"])
def test_bounds_enforced(bad):
    with pytest.raises(ValidationError):
        Estimate(kind="point", value=Decimal(bad), source={"type": "hypothetical",
                                                           "assumption": "x"})


def test_range_ordering():
    src = {"type": "hypothetical", "assumption": "x"}
    Estimate(kind="range", low=Decimal("0.1"), high=Decimal("0.2"), source=src)
    with pytest.raises(ValidationError):
        Estimate(kind="range", low=Decimal("0.3"), high=Decimal("0.2"), source=src)
    with pytest.raises(ValidationError):
        Estimate(kind="range", low=Decimal("0.2"), high=Decimal("0.2"), source=src)


def test_measured_provenance_validated():
    ok = dict(data_source="Release log", count=3, denominator=40,
              date_from=date(2026, 1, 1), date_to=date(2026, 6, 30), context="Web checkout")
    MeasuredSource(**ok)
    with pytest.raises(ValidationError):
        MeasuredSource(**ok | {"count": 41})
    with pytest.raises(ValidationError):
        MeasuredSource(**ok | {"denominator": 0})
    with pytest.raises(ValidationError):
        MeasuredSource(**ok | {"date_from": date(2026, 7, 1)})


def test_expert_provenance_requires_reviewer_and_rationale():
    with pytest.raises(ValidationError):
        ExpertSource(reviewer="", rationale="x", review_date=date(2026, 1, 1))


def test_reviewed_estimate_needs_reviewer():
    with pytest.raises(ValidationError):
        Estimate(kind="point", value=Decimal("0.5"), verification="reviewed",
                 source={"type": "hypothetical", "assumption": "x"})


# ------------------------------------------------------------ AI suggestions


def _transition(**kw) -> Transition:
    base = dict(id=new_id("transition"), source_node_id="nod_aaaaaaaa",
                target_node_id="nod_bbbbbbbb", outcome="failure", trigger="API mismatch")
    return Transition(**(base | kw))


def test_ai_suggestion_cannot_carry_unreviewed_number():
    _transition(origin="ai")  # unknown probability is fine
    with pytest.raises(ValidationError):
        _transition(origin="ai", probability=Estimate.hypothetical("10", "model guess"))
    reviewed = Estimate.hypothetical("10", "x").model_copy(
        update={"verification": "reviewed", "reviewed_by": "Ana", "reviewed_on": date(2026, 1, 1)})
    _transition(origin="ai", probability=reviewed)


def test_ai_risk_defaults_to_unknown_likelihood():
    r = RiskCandidate(id=new_id("risk"), category="api_mismatch", title="t", trigger="t",
                      rationale="r", consequence="c", origin="ai")
    assert r.likelihood.kind == "unknown" and r.review_status == "suggested"


# ------------------------------------------------------------ nodes


def test_goal_node_must_be_terminal():
    with pytest.raises(ValidationError):
        ScenarioNode(id=new_id("node"), kind="goal", label="Goal")
    ScenarioNode(id=new_id("node"), kind="goal", label="Goal", terminal=True)


# ------------------------------------------------------------ versions


def _version(**kw) -> ScenarioVersion:
    base = dict(id=new_id("version"), project_id="prj_aaaaaaaa", number=1, label="Baseline",
                kind="baseline", created_by=OWNER, created_at=datetime.now(UTC),
                plan=plan(tasks("A")), graph=ScenarioGraph())
    return ScenarioVersion(**(base | kw))


def test_version_is_frozen_and_hashed():
    v = _version()
    assert len(v.content_hash) == 64 and v.verify()
    with pytest.raises(ValidationError):
        v.label = "changed"


def test_version_detects_nested_mutation():
    v = _version()
    v.plan.tasks[0].__dict__["title"] = "tampered"
    assert not v.verify()


def test_version_rejects_wrong_hash_on_load():
    v = _version()
    data = v.model_dump(mode="json")
    data["plan"]["project"]["goal"] = "Different goal"
    with pytest.raises(ValidationError):
        ScenarioVersion.model_validate(data)


# ------------------------------------------------------------ outcomes


def test_outcome_counts_validated():
    base = dict(id=new_id("outcome"), version_id="ver_aaaaaaaa", decision_group_id="dgp_aaaaaaaa",
                observed_on=date(2026, 11, 1), exposure=10, occurrences=2, recorded_by=OWNER)
    ObservedOutcome(**base)
    with pytest.raises(ValidationError):
        ObservedOutcome(**base | {"occurrences": 11})
