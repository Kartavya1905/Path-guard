"""Reports, CSV export, reviewer sign-off and outcome logging through the API."""

import csv
import io

import pytest

from tests.conftest import OUTSIDER, PLANNER, REVIEWER


@pytest.fixture
def seeded(api):
    from app.db import get_sessionmaker
    from app.examples_seed import seed_examples

    with get_sessionmaker()() as session:
        seed_examples(session, "usr_demo0001")
    projects = {p["title"]: p["id"] for p in api.get("/api/projects", headers=PLANNER).json()}
    return projects["Checkout launch (example)"], projects["Checkout transaction (example)"]


def versions(api, pid):
    return api.get(f"/api/projects/{pid}/versions", headers=PLANNER).json()


def test_working_map_report_is_a_labeled_draft(api, seeded):
    launch, _ = seeded
    rep = api.get(f"/api/projects/{launch}/report", headers=PLANNER).json()
    assert rep["mode"] == "draft" and rep["status_label"].startswith("Draft")
    assert rep["analysis"]["first_pass"]["value"] == "0.72675"
    assert rep["explorer"]["risks"][0]["impact"] == "critical"
    assert rep["evidence_gaps"] and rep["estimate_sources"]
    assert api.get(f"/api/projects/{launch}/report", headers=OUTSIDER).status_code == 404


def test_report_with_comparison(api, seeded):
    launch, _ = seeded
    v1, v2 = versions(api, launch)
    rep = api.get(
        f"/api/projects/{launch}/report?version={v1['id']}&compare_with={v2['id']}", headers=PLANNER
    ).json()
    assert rep["comparison"]["first_pass_delta"]["pp"] == "8.55"
    assert rep["mode"] == "draft" and "no reviewer sign-off" in rep["status_label"]


def test_signoff_needs_a_reviewable_version_and_the_review_role(api, seeded):
    launch, tx = seeded
    v1 = versions(api, launch)[0]["id"]
    res = api.post(f"/api/projects/{launch}/versions/{v1}/signoff", json={}, headers=PLANNER)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "not_reviewable"

    tv = versions(api, tx)[0]["id"]
    api.post(
        f"/api/projects/{tx}/members",
        json={"user_id": "usr_demo0002", "role": "editor"},
        headers=PLANNER,
    )
    assert (
        api.post(f"/api/projects/{tx}/versions/{tv}/signoff", json={}, headers=REVIEWER).status_code
        == 403
    )
    api.post(
        f"/api/projects/{tx}/members",
        json={"user_id": "usr_demo0002", "role": "reviewer"},
        headers=PLANNER,
    )
    res = api.post(
        f"/api/projects/{tx}/versions/{tv}/signoff", json={"note": "Checked"}, headers=REVIEWER
    )
    assert res.status_code == 201, res.text
    assert res.json()["reviewer_name"] == "Demo Reviewer"

    rep = api.get(f"/api/projects/{tx}/report?version={tv}", headers=PLANNER).json()
    assert rep["mode"] == "reviewed" and "signed off by Demo Reviewer" in rep["status_label"]
    csv_res = api.get(f"/api/projects/{tx}/report/risks.csv?version={tv}", headers=PLANNER)
    assert "reviewed-risk-register.csv" in csv_res.headers["content-disposition"]
    actions = [e["action"] for e in api.get(f"/api/projects/{tx}/audit", headers=PLANNER).json()]
    assert "report.signed_off" in actions


def test_csv_export_escapes_user_text(api, seeded):
    launch, _ = seeded
    graph = api.get(f"/api/projects/{launch}/graph", headers=PLANNER).json()
    risk = graph["graph"]["risks"][0]
    risk["title"] = 'Rollback, "fast"\nor slow'
    risk["trigger"] = "=1+1"
    api.put(
        f"/api/projects/{launch}/graph",
        json={"graph": graph["graph"], "revision": graph["revision"]},
        headers=PLANNER,
    )
    res = api.get(f"/api/projects/{launch}/report/risks.csv", headers=PLANNER)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert "draft-risk-register.csv" in res.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(res.text.lstrip("﻿"))))
    row = next(r for r in rows[1:] if r[2].startswith("Rollback"))
    assert row[2] == 'Rollback, "fast"\nor slow' and row[9] == "'=1+1"


def tx_links(api, tx):
    v = versions(api, tx)[0]
    g = api.get(f"/api/projects/{tx}/versions/{v['id']}", headers=PLANNER).json()["graph"]
    labels = {n["id"]: n["label"] for n in g["nodes"]}

    def find(src, dst):
        return next(
            t
            for t in g["transitions"]
            if labels[t["source_node_id"]] == src and labels[t["target_node_id"]] == dst
        )

    return (
        v["id"],
        find("Authorize payment", "Confirm order"),
        find("Authorize payment", "Payment declined"),
    )


def record(api, tx, version_id, t, occ, exp, ctx=("web",), headers=PLANNER):
    return api.post(
        f"/api/projects/{tx}/outcomes",
        headers=headers,
        json={
            "version_id": version_id,
            "decision_group_id": t["decision_group_id"],
            "transition_id": t["id"],
            "observed_on": "2026-11-20",
            "period_start": "2026-11-01",
            "period_end": "2026-11-20",
            "exposure": exp,
            "occurrences": occ,
            "context": list(ctx),
            "notes": "Week of launch",
        },
    )


def test_outcomes_to_an_approved_estimate(api, seeded):
    _, tx = seeded
    vid, ok, declined = tx_links(api, tx)
    assert record(api, tx, vid, ok, 185, 200).status_code == 201
    assert record(api, tx, vid, ok, 5, 10, ctx=("mobile",)).status_code == 201

    p = api.get(
        f"/api/projects/{tx}/outcomes/proposal?transition_id={ok['id']}&context=web",
        headers=PLANNER,
    ).json()
    assert p["status"] == "candidate" and p["candidate"]["value"] == "0.925"
    assert p["candidate"]["source"]["count"] == 185 and len(p["excluded"]) == 1
    mobile = api.get(
        f"/api/projects/{tx}/outcomes/proposal?transition_id={ok['id']}&context=mobile",
        headers=PLANNER,
    ).json()
    assert mobile["status"] == "unresolved"

    revision = api.get(f"/api/projects/{tx}/graph", headers=PLANNER).json()["revision"]
    body = {"transition_id": ok["id"], "context": ["web"], "revision": revision}
    res = api.post(f"/api/projects/{tx}/outcomes/apply", json=body, headers=PLANNER)
    assert res.status_code == 422 and "Payment declined" in res.json()["detail"]["message"]

    body["sibling_changes"] = [
        {
            "transition_id": declined["id"],
            "new_probability": {
                "kind": "point",
                "value": "0.075",
                "source": {"type": "hypothetical", "assumption": "The remainder of observed runs"},
            },
        }
    ]
    res = api.post(f"/api/projects/{tx}/outcomes/apply", json=body, headers=PLANNER)
    assert res.status_code == 200, res.text
    g = res.json()["graph"]
    t = next(x for x in g["transitions"] if x["id"] == ok["id"])
    assert t["probability"]["source"]["type"] == "measured"
    assert t["probability"]["verification"] == "reviewed"
    assert t["probability"]["reviewed_by"] == "Demo Planner"
    assert any(e["title"].startswith("Observed outcomes") for e in g["evidence"])
    vs = versions(api, tx)
    assert vs[-1]["label"] == "Before outcome update" and vs[-1]["kind"] == "snapshot"
    old = api.get(f"/api/projects/{tx}/versions/{vs[0]['id']}", headers=PLANNER).json()
    old_t = next(x for x in old["graph"]["transitions"] if x["id"] == ok["id"])
    assert old_t["probability"]["source"]["type"] == "hypothetical"  # preserved
    assert res.json()["readiness"]["can_analyze"]


def test_outcome_validation_and_roles(api, seeded):
    _, tx = seeded
    vid, ok, declined = tx_links(api, tx)
    bad = dict(ok, decision_group_id="dgp_notreal0001")
    assert record(api, tx, vid, bad, 1, 2).status_code == 422
    assert record(api, tx, vid, ok, 5, 2).status_code == 422  # occurrences > exposure
    assert record(api, tx, vid, ok, 1, 2, headers=OUTSIDER).status_code == 404

    api.post(
        f"/api/projects/{tx}/members",
        json={"user_id": "usr_demo0002", "role": "editor"},
        headers=PLANNER,
    )
    revision = api.get(f"/api/projects/{tx}/graph", headers=PLANNER).json()["revision"]
    res = api.post(
        f"/api/projects/{tx}/outcomes/apply",
        headers=REVIEWER,
        json={"transition_id": ok["id"], "revision": revision},
    )
    assert res.status_code == 403  # editors can record, only reviewers/owners approve
