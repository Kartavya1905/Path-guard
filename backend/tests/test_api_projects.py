"""Persistence, revision conflicts, versions, access control and audit."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import reset_caches
from tests.conftest import OUTSIDER, PLANNER, REVIEWER

MIN = {"start": "Checkout change approved", "goal": "Reliable launch"}


def create(api, **extra):
    res = api.post("/api/projects", json=MIN | extra, headers=PLANNER)
    assert res.status_code == 201, res.text
    return res.json()


def tasks_payload():
    return [
        {"id": "tsk_qa000001", "title": "QA and staged rollout", "position": 0,
         "duration_days": "3.5"},
        {"id": "tsk_req00001", "title": "Confirm requirements", "position": 1},
        {"id": "tsk_build001", "title": "Build integration", "position": 2, "owner": "Sam"},
    ]


def prereqs_payload():
    return [
        {"id": "pre_00000001", "before_task_id": "tsk_req00001", "after_task_id": "tsk_build001"},
        {"id": "pre_00000002", "before_task_id": "tsk_build001", "after_task_id": "tsk_qa000001"},
    ]


def save(api, pid, revision, headers=PLANNER, **fields):
    body = MIN | {"tasks": [], "prerequisites": []} | fields | {"revision": revision}
    return api.put(f"/api/projects/{pid}", json=body, headers=headers)


# --------------------------------------------------------------------------- drafts


def test_create_with_only_start_and_goal(api):
    out = create(api)
    plan = out["plan"]
    assert plan["tasks"] == [] and plan["prerequisites"] == []
    assert plan["project"]["revision"] == 1 and plan["project"]["title"] == "Reliable launch"
    assert out["issues"] == [] and out["role"] == "owner"


@pytest.mark.parametrize("body", [{"start": "x"}, {"goal": "x"}, {"start": " ", "goal": "x"}])
def test_start_and_goal_required(api, body):
    assert api.post("/api/projects", json=body, headers=PLANNER).status_code == 422


def test_draft_round_trip_keeps_display_order(api):
    pid = create(api)["plan"]["project"]["id"]
    res = save(api, pid, 1, tasks=tasks_payload(), prerequisites=prereqs_payload(),
               success_criteria="No checkout errors", context={"domain": "checkout"})
    assert res.status_code == 200, res.text
    plan = api.get(f"/api/projects/{pid}", headers=PLANNER).json()["plan"]
    assert [t["title"] for t in plan["tasks"]] == [
        "QA and staged rollout", "Confirm requirements", "Build integration"]
    assert plan["tasks"][0]["duration_days"] == "3.50"
    assert len(plan["prerequisites"]) == 2
    assert plan["project"]["context"]["domain"] == "checkout"
    assert plan["project"]["revision"] == 2


def test_cycle_is_saved_with_issue(api):
    pid = create(api)["plan"]["project"]["id"]
    ts = tasks_payload()[:2]
    cyc = [{"id": "pre_00000001", "before_task_id": ts[0]["id"], "after_task_id": ts[1]["id"]},
           {"id": "pre_00000002", "before_task_id": ts[1]["id"], "after_task_id": ts[0]["id"]}]
    out = save(api, pid, 1, tasks=ts, prerequisites=cyc).json()
    assert [i["code"] for i in out["issues"]] == ["dependency_cycle"]


def test_dangling_prerequisite_rejected_without_changes(api):
    pid = create(api)["plan"]["project"]["id"]
    bad = [{"id": "pre_00000001", "before_task_id": "tsk_missing0",
            "after_task_id": "tsk_req00001"}]
    res = save(api, pid, 1, tasks=tasks_payload(), prerequisites=bad)
    assert res.status_code == 422 and res.json()["detail"]["code"] == "plan_unstorable"
    plan = api.get(f"/api/projects/{pid}", headers=PLANNER).json()["plan"]
    assert plan["project"]["revision"] == 1


def test_failed_multi_record_write_rolls_back(api):
    pid = create(api)["plan"]["project"]["id"]
    assert save(api, pid, 1, tasks=tasks_payload()).status_code == 200
    dup = prereqs_payload()
    dup[1]["id"] = dup[0]["id"]  # duplicate primary key fails at the database, after
    renamed = [t | {"title": t["title"] + " v2"} for t in tasks_payload()]  # tasks were written
    res = save(api, pid, 2, title="changed", tasks=renamed, prerequisites=dup)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "integrity_error"
    plan = api.get(f"/api/projects/{pid}", headers=PLANNER).json()["plan"]
    assert plan["project"]["revision"] == 2 and plan["project"]["title"] == "Reliable launch"
    assert [t["title"] for t in plan["tasks"]] == [t["title"] for t in tasks_payload()]
    assert plan["prerequisites"] == []


def test_stale_revision_gets_409_and_keeps_newer_draft(api):
    pid = create(api)["plan"]["project"]["id"]
    assert save(api, pid, 1, tasks=tasks_payload()).status_code == 200  # tab A
    res = save(api, pid, 1, tasks=[])  # tab B, still on revision 1
    assert res.status_code == 409
    assert res.json()["detail"] == res.json()["detail"] | {
        "code": "revision_conflict", "current_revision": 2}
    assert len(api.get(f"/api/projects/{pid}", headers=PLANNER).json()["plan"]["tasks"]) == 3


# --------------------------------------------------------------------------- graph

def graph_doc(evidence_quality="moderate", risk_status="suggested", verification=None):
    prob = {"kind": "point", "value": "0.85",
            "source": {"type": "hypothetical", "assumption": "Seeded example"}}
    if verification:
        prob |= {"verification": "reviewed", "reviewed_by": "Ana", "reviewed_on": "2026-11-20"}
    return {
        "nodes": [
            {"id": "nod_start001", "kind": "start", "label": "Start",
             "layout": {"x": 0, "y": 0}},
            {"id": "nod_goal0001", "kind": "goal", "label": "Goal", "terminal": True,
             "layout": {"x": 300, "y": 0}, "collapsed": True},
        ],
        "transitions": [
            {"id": "trn_00000001", "source_node_id": "nod_start001",
             "target_node_id": "nod_goal0001", "outcome": "success",
             "trigger": "Requirements confirmed", "probability": prob},
        ],
        "risks": [
            {"id": "rsk_00000001", "category": "api_mismatch", "title": "API mismatch",
             "trigger": "Provider changes schema", "rationale": "r", "consequence": "c",
             "origin": "rule", "review_status": risk_status},
        ],
        "evidence": [{"id": "evd_00000001", "title": "Past launches", "kind": "dataset",
                      "quality": evidence_quality}],
        "evidence_links": [{"id": "evl_00000001", "evidence_id": "evd_00000001",
                            "target_kind": "transition", "target_id": "trn_00000001"}],
        "notes": [{"id": "not_00000001", "target_kind": "risk", "target_id": "rsk_00000001",
                   "author_id": "usr_demo0001", "body": "Check with payments team",
                   "created_at": "2026-11-26T10:00:00Z"}],
    }


def test_graph_round_trip_with_layout_notes_and_evidence(api):
    pid = create(api)["plan"]["project"]["id"]
    assert api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json()["graph"]["nodes"] == []
    res = api.put(f"/api/projects/{pid}/graph", json={"graph": graph_doc(), "revision": 1},
                  headers=PLANNER)
    assert res.status_code == 200 and res.json()["revision"] == 2
    g = api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json()["graph"]
    assert g["nodes"][1]["layout"] == {"x": 300.0, "y": 0.0} and g["nodes"][1]["collapsed"]
    assert g["transitions"][0]["probability"]["value"] == "0.85"
    assert g["notes"][0]["body"] == "Check with payments team"
    assert g["evidence_links"][0]["target_id"] == "trn_00000001"


def test_graph_and_draft_share_revision(api):
    pid = create(api)["plan"]["project"]["id"]
    assert save(api, pid, 1).status_code == 200
    res = api.put(f"/api/projects/{pid}/graph", json={"graph": graph_doc(), "revision": 1},
                  headers=PLANNER)
    assert res.status_code == 409


def test_invalid_graph_rejected(api):
    pid = create(api)["plan"]["project"]["id"]
    bad = graph_doc()
    bad["transitions"][0]["probability"]["value"] = "1.5"
    res = api.put(f"/api/projects/{pid}/graph", json={"graph": bad, "revision": 1},
                  headers=PLANNER)
    assert res.status_code == 422


def test_audit_records_evidence_review_and_approval(api):
    pid = create(api)["plan"]["project"]["id"]
    put = lambda g, r: api.put(f"/api/projects/{pid}/graph",  # noqa: E731
                               json={"graph": g, "revision": r}, headers=PLANNER)
    assert put(graph_doc(), 1).status_code == 200
    assert put(graph_doc(evidence_quality="strong", risk_status="accepted",
                         verification=True), 2).status_code == 200
    actions = [e["action"] for e in api.get(f"/api/projects/{pid}/audit", headers=PLANNER).json()]
    assert actions == ["project.created", "evidence.added", "evidence_link.added",
                       "estimate.reviewed", "evidence.changed", "review.decision"]


# --------------------------------------------------------------------------- versions


def test_versions_are_immutable_snapshots(api, pg_engine):
    pid = create(api)["plan"]["project"]["id"]
    assert save(api, pid, 1, tasks=tasks_payload()).status_code == 200
    base = api.post(f"/api/projects/{pid}/versions",
                    json={"label": "Baseline", "kind": "baseline", "revision": 2},
                    headers=PLANNER)
    assert base.status_code == 201, base.text
    base = base.json()
    assert base["number"] == 1 and len(base["content_hash"]) == 64

    assert save(api, pid, 2, tasks=[], goal="Changed goal").status_code == 200
    again = api.get(f"/api/projects/{pid}/versions/{base['id']}", headers=PLANNER).json()
    assert again["plan"]["project"]["goal"] == "Reliable launch"
    assert len(again["plan"]["tasks"]) == 3
    assert again["content_hash"] == base["content_hash"]

    with pg_engine.begin() as conn, pytest.raises(DBAPIError, match="immutable"):
        conn.execute(text("UPDATE scenario_versions SET label = 'x'"))


def test_variant_needs_baseline_and_current_revision(api):
    pid = create(api)["plan"]["project"]["id"]
    url = f"/api/projects/{pid}/versions"
    assert api.post(url, json={"label": "V", "kind": "variant", "revision": 1},
                    headers=PLANNER).status_code == 422
    base = api.post(url, json={"label": "B", "kind": "baseline", "revision": 1},
                    headers=PLANNER).json()
    assert api.post(url, json={"label": "V", "kind": "variant", "revision": 0,
                               "parent_version_id": base["id"]}, headers=PLANNER).status_code == 409
    var = api.post(url, json={"label": "V", "kind": "variant", "revision": 1,
                              "parent_version_id": base["id"]}, headers=PLANNER)
    assert var.status_code == 201 and var.json()["number"] == 2
    listed = api.get(url, headers=PLANNER).json()
    assert [(v["number"], v["kind"]) for v in listed] == [(1, "baseline"), (2, "variant")]
    actions = [e["action"] for e in api.get(f"/api/projects/{pid}/audit", headers=PLANNER).json()]
    assert actions.count("version.published") == 2


# --------------------------------------------------------------------------- access


def test_outsider_cannot_see_or_edit(api):
    pid = create(api)["plan"]["project"]["id"]
    assert api.get(f"/api/projects/{pid}", headers=OUTSIDER).status_code == 404
    assert save(api, pid, 1, headers=OUTSIDER).status_code == 404
    assert api.get(f"/api/projects/{pid}/graph", headers=OUTSIDER).status_code == 404
    assert api.get(f"/api/projects/{pid}/audit", headers=OUTSIDER).status_code == 404
    assert api.get("/api/projects", headers=OUTSIDER).json() == []
    assert len(api.get("/api/projects", headers=PLANNER).json()) == 1


def test_roles_limit_what_members_can_do(api):
    pid = create(api)["plan"]["project"]["id"]
    assert api.post(f"/api/projects/{pid}/members", json={"user_id": "usr_demo0002",
                    "role": "reviewer"}, headers=REVIEWER).status_code == 404
    assert api.post(f"/api/projects/{pid}/members", json={"user_id": "usr_demo0002",
                    "role": "reviewer"}, headers=PLANNER).status_code == 204
    assert api.get(f"/api/projects/{pid}", headers=REVIEWER).json()["role"] == "reviewer"
    assert save(api, pid, 1, headers=REVIEWER).status_code == 403
    assert api.post(f"/api/projects/{pid}/versions", json={"label": "B", "revision": 1},
                    headers=REVIEWER).status_code == 403
    assert api.delete(f"/api/projects/{pid}", headers=REVIEWER).status_code == 403

    assert api.delete(f"/api/projects/{pid}", headers=PLANNER).status_code == 204
    assert api.get(f"/api/projects/{pid}", headers=PLANNER).status_code == 404


def test_unknown_demo_user_rejected(api):
    assert api.get("/api/me", headers={"X-PathGuard-Demo-User": "usr_nobody00"}).status_code == 401
    assert api.get("/api/me").json()["id"] == "usr_demo0001"


def test_demo_identity_off_rejects_requests(api, monkeypatch):
    monkeypatch.setenv("PATHGUARD_DEMO_IDENTITY_ENABLED", "false")
    reset_caches()
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/projects").status_code == 401
    monkeypatch.undo()
    reset_caches()


def test_audit_is_append_only(api, pg_engine):
    create(api)
    with pg_engine.begin() as conn, pytest.raises(DBAPIError, match="immutable"):
        conn.execute(text("DELETE FROM audit_events"))
