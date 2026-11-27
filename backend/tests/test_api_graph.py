"""Map generation, suggestion review, edit tracking and readiness through the API."""

from tests.conftest import OUTSIDER, PLANNER, REVIEWER
from tests.test_api_projects import create, prereqs_payload, save, tasks_payload


def checkout_project(api) -> tuple[str, int]:
    pid = create(api)["plan"]["project"]["id"]
    res = save(
        api,
        pid,
        1,
        tasks=tasks_payload(),
        prerequisites=prereqs_payload(),
        context={"domain": "checkout"},
    )
    assert res.status_code == 200, res.text
    return pid, res.json()["plan"]["project"]["revision"]


def generate(api, pid, revision, headers=PLANNER):
    return api.post(
        f"/api/projects/{pid}/graph/generate", json={"revision": revision}, headers=headers
    )


def review(api, pid, risk_id, revision, headers=PLANNER, **body):
    return api.post(
        f"/api/projects/{pid}/risks/{risk_id}/review",
        json={"decision": "accept", "revision": revision} | body,
        headers=headers,
    )


def test_empty_project_has_no_map_yet(api):
    pid = create(api)["plan"]["project"]["id"]
    out = api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json()
    assert out["readiness"]["can_analyze"] is False
    assert out["readiness"]["blockers"] == [
        "No scenario map yet. Generate one from the plan first."
    ]


def test_generate_zero_task_project(api):
    pid = create(api)["plan"]["project"]["id"]
    res = generate(api, pid, 1)
    assert res.status_code == 200, res.text
    out = res.json()
    assert [n["kind"] for n in out["graph"]["nodes"]] == ["start", "goal"]
    assert out["revision"] == 2 and len(out["added"]) == 3
    assert out["readiness"]["can_analyze"] and out["readiness"]["can_mark_reviewed"]
    actions = [e["action"] for e in api.get(f"/api/projects/{pid}/audit", headers=PLANNER).json()]
    assert "graph.generated" in actions


def test_checkout_suggestions_review_and_regenerate(api):
    pid, rev = checkout_project(api)
    out = generate(api, pid, rev).json()
    risks = {r["category"]: r for r in out["graph"]["risks"]}
    assert set(risks) >= {
        "unclear_requirements",
        "api_mismatch",
        "incomplete_testing",
        "release_defect",
    }
    assert all(r["review_status"] == "suggested" for r in risks.values())
    assert all(r["likelihood"]["kind"] == "unknown" for r in risks.values())
    assert out["readiness"]["can_analyze"] and not out["readiness"]["can_mark_reviewed"]

    target = risks["release_defect"]
    res = review(api, pid, target["id"], out["revision"], model_branch=True)
    assert res.status_code == 200, res.text
    out = res.json()
    branch = [t for t in out["graph"]["transitions"] if t["rule_key"] == target["dedupe_key"]]
    assert branch and all(t["review_status"] == "accepted" for t in branch)
    assert out["readiness"]["issues"] == [] or all(
        i["severity"] == "warning" for i in out["readiness"]["issues"]
    )

    res = review(api, pid, risks["api_mismatch"]["id"], out["revision"], decision="reject")
    out = res.json()
    again = generate(api, pid, out["revision"]).json()
    statuses = {r["category"]: r["review_status"] for r in again["graph"]["risks"]}
    assert statuses["release_defect"] == "accepted" and statuses["api_mismatch"] == "rejected"
    assert again["added"] == [] and again["removed"] == []

    audit = api.get(f"/api/projects/{pid}/audit", headers=PLANNER).json()
    decisions = [e for e in audit if e["action"] == "review.decision"]
    assert {e["detail"]["to"] for e in decisions} >= {"accepted", "rejected"}


def test_editing_a_rule_suggestion_survives_regeneration(api):
    pid, rev = checkout_project(api)
    out = generate(api, pid, rev).json()
    graph = out["graph"]
    graph["risks"][0]["title"] = "Our own wording"
    res = api.put(
        f"/api/projects/{pid}/graph",
        json={"graph": graph, "revision": out["revision"]},
        headers=PLANNER,
    )
    assert res.status_code == 200, res.text
    saved = res.json()
    assert saved["graph"]["risks"][0]["review_status"] == "edited"
    again = generate(api, pid, saved["revision"]).json()
    risk = next(r for r in again["graph"]["risks"] if r["id"] == graph["risks"][0]["id"])
    assert risk["title"] == "Our own wording" and risk["review_status"] == "edited"


def test_incomplete_map_saves_with_clear_messages(api):
    pid = create(api)["plan"]["project"]["id"]
    graph = {"nodes": [{"id": "nod_start0001", "kind": "start", "label": "Here"}]}
    res = api.put(
        f"/api/projects/{pid}/graph", json={"graph": graph, "revision": 1}, headers=PLANNER
    )
    assert res.status_code == 200, res.text
    r = res.json()["readiness"]
    assert not r["can_analyze"]
    codes = {i["code"] for i in r["issues"]}
    assert {"goal_missing", "dead_end"} <= codes
    assert "must be fixed before the map can be analyzed" in r["blockers"][0]


def test_generate_and_review_need_the_current_revision(api):
    pid = create(api)["plan"]["project"]["id"]
    assert generate(api, pid, 0).json()["detail"]["code"] == "revision_conflict"
    pid, rev = checkout_project(api)
    out = generate(api, pid, rev).json()
    risk = out["graph"]["risks"][0]["id"]
    res = review(api, pid, risk, out["revision"] - 1)
    assert res.status_code == 409


def test_review_unknown_risk_is_404(api):
    pid = create(api)["plan"]["project"]["id"]
    out = generate(api, pid, 1).json()
    assert review(api, pid, "rsk_unknown0001", out["revision"]).status_code == 404


def test_roles_for_generate_and_review(api):
    pid, rev = checkout_project(api)
    out = generate(api, pid, rev).json()
    risk = out["graph"]["risks"][0]["id"]
    assert generate(api, pid, out["revision"], headers=OUTSIDER).status_code == 404

    api.post(
        f"/api/projects/{pid}/members",
        json={"user_id": "usr_demo0002", "role": "viewer"},
        headers=PLANNER,
    )
    assert review(api, pid, risk, out["revision"], headers=REVIEWER).status_code == 403

    api.post(
        f"/api/projects/{pid}/members",
        json={"user_id": "usr_demo0002", "role": "reviewer"},
        headers=PLANNER,
    )
    assert generate(api, pid, out["revision"], headers=REVIEWER).status_code == 403
    res = review(api, pid, risk, out["revision"], headers=REVIEWER)
    assert res.status_code == 200, res.text


def test_review_notes_are_stamped_and_append_only(api):
    pid = create(api)["plan"]["project"]["id"]
    out = generate(api, pid, 1).json()
    graph = out["graph"]
    node = graph["nodes"][0]["id"]
    graph["notes"] = [
        {
            "id": "not_note00001",
            "target_kind": "node",
            "target_id": node,
            "author_id": "usr_demo0003",  # spoofed author is replaced
            "body": "Check the payment sandbox first",
            "created_at": "2000-01-01T00:00:00Z",
        }
    ]
    res = api.put(
        f"/api/projects/{pid}/graph",
        json={"graph": graph, "revision": out["revision"]},
        headers=PLANNER,
    )
    saved = res.json()
    note = saved["graph"]["notes"][0]
    assert note["author_id"] == "usr_demo0001" and not note["created_at"].startswith("2000")

    graph = saved["graph"]
    graph["notes"] = []  # deleting is ignored
    res = api.put(
        f"/api/projects/{pid}/graph",
        json={"graph": graph, "revision": saved["revision"]},
        headers=PLANNER,
    )
    assert [n["id"] for n in res.json()["graph"]["notes"]] == ["not_note00001"]
