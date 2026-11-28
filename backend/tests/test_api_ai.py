"""Optional AI suggestions through the API, with a fake provider (no model needed)."""

import json

import pytest

from app.ai import ProviderError
from app.db import reset_caches
from tests.conftest import PLANNER, REVIEWER
from tests.test_api_graph import checkout_project, generate
from tests.test_api_projects import MIN, prereqs_payload, tasks_payload


class FakeProvider:
    name = "ollama"

    def __init__(self, reply: str | Exception, *, remote: bool = False, model: str = "fake"):
        self.reply, self.remote, self.model, self.calls = reply, remote, model, 0

    messages: list[dict] | None = None

    def complete(self, messages, schema, max_tokens, timeout):
        self.calls += 1
        self.messages = messages
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def ai_on(api, monkeypatch):
    monkeypatch.setenv("PATHGUARD_AI_ENABLED", "true")
    reset_caches()

    def use(provider: FakeProvider) -> FakeProvider:
        monkeypatch.setattr("app.api_ai.get_provider", lambda settings: provider)
        return provider

    yield use
    monkeypatch.undo()
    reset_caches()


def mapped_project(api):
    pid, rev = checkout_project(api)
    out = generate(api, pid, rev).json()
    graph = out["graph"]
    step = next(n for n in graph["nodes"] if n["label"] == "Build integration")
    return pid, out["revision"], step["id"]


def hazard(node_id: str, title: str = "Webhook retries duplicate orders", **kw) -> str:
    return json.dumps(
        {
            "suggestions": [
                {
                    "type": "hazard",
                    "title": title,
                    "affected_node_id": node_id,
                    "trigger": "The provider resends a webhook",
                    "consequence": "Customers are charged twice",
                    "rationale": "Webhooks are delivered at least once",
                    "control": "Idempotency keys",
                    "impact": "critical",
                }
                | kw
            ]
        }
    )


def ask(api, pid, revision, focus=None):
    return api.post(
        f"/api/projects/{pid}/ai/suggestions",
        json={"revision": revision, "focus_node_id": focus},
        headers=PLANNER,
    )


def test_ai_is_off_by_default(api):
    pid, rev, step = mapped_project(api)
    res = ask(api, pid, rev, step)
    assert res.status_code == 409 and res.json()["detail"]["code"] == "ai_disabled"
    assert api.get("/api/health").json()["ai"]["enabled"] is False


def test_suggestions_join_the_review_queue_and_never_carry_numbers(api, ai_on):
    pid, rev, step = mapped_project(api)
    fake = ai_on(
        FakeProvider(
            json.dumps(
                {
                    "suggestions": [
                        json.loads(hazard(step))["suggestions"][0],
                        json.loads(hazard(step, title="Fraud", consequence="Roughly 5% of orders"))[
                            "suggestions"
                        ][0],
                    ]
                }
            )
        )
    )
    res = ask(api, pid, rev, step)
    assert res.status_code == 200, res.text
    out = res.json()
    assert len(out["added"]) == 1 and len(out["rejected"]) == 1 and not out["cached"]
    risk = next(r for r in out["graph"]["risks"] if r["id"] == out["added"][0])
    assert risk["origin"] == "ai" and risk["review_status"] == "suggested"
    assert risk["likelihood"]["kind"] == "unknown"
    assert out["revision"] == rev + 1 and fake.calls == 1

    # Accepting goes through the same review route as rule suggestions.
    res = api.post(
        f"/api/projects/{pid}/risks/{risk['id']}/review",
        json={"decision": "accept", "revision": out["revision"]},
        headers=PLANNER,
    )
    assert res.status_code == 200
    actions = [e["action"] for e in api.get(f"/api/projects/{pid}/audit", headers=PLANNER).json()]
    assert "ai.suggestions" in actions


def test_identical_requests_reuse_the_cached_reply(api, ai_on):
    pid, rev, step = mapped_project(api)
    fake = ai_on(FakeProvider(json.dumps({"suggestions": []})))
    first = ask(api, pid, rev, step).json()
    second = ask(api, pid, first["revision"], step).json()
    assert first["cached"] is False and second["cached"] is True and fake.calls == 1


def test_reads_and_recalculation_never_call_the_model(api, ai_on):
    pid, rev, _ = mapped_project(api)
    fake = ai_on(FakeProvider(json.dumps({"suggestions": []})))
    for path in ("graph", "analysis", "explorer", "report"):
        assert api.get(f"/api/projects/{pid}/{path}", headers=PLANNER).status_code == 200
    graph = api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json()
    api.put(
        f"/api/projects/{pid}/graph",
        json={"graph": graph["graph"], "revision": rev},
        headers=PLANNER,
    )
    assert fake.calls == 0


@pytest.mark.parametrize(
    "reply,status,code",
    [
        (ProviderError("The model didn't answer within 120 seconds."), 503, "ai_unavailable"),
        ("this is not json", 502, "ai_invalid_output"),
        (
            json.dumps({"suggestions": [{"type": "hazard", "probability": 0.4}]}),
            502,
            "ai_invalid_output",
        ),
    ],
)
def test_failures_leave_the_map_unchanged(api, ai_on, reply, status, code):
    pid, rev, step = mapped_project(api)
    ai_on(FakeProvider(reply))
    before = api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json()
    res = ask(api, pid, rev, step)
    assert res.status_code == status and res.json()["detail"]["code"] == code
    assert "Nothing was changed" in res.json()["detail"]["message"]
    assert api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json() == before


def test_remote_model_needs_server_and_project_opt_in(api, ai_on, monkeypatch):
    pid, rev, step = mapped_project(api)
    fake = ai_on(FakeProvider(hazard(step), remote=True))
    res = ask(api, pid, rev, step)
    assert res.status_code == 403 and fake.calls == 0

    monkeypatch.setenv("PATHGUARD_EXTERNAL_PROCESSING_ENABLED", "true")
    reset_caches()
    assert ask(api, pid, rev, step).status_code == 403  # the project hasn't opted in yet
    draft = api.get(f"/api/projects/{pid}", headers=PLANNER).json()["plan"]
    p = draft["project"]
    body = {
        "title": p["title"],
        "start": p["start"],
        "goal": p["goal"],
        "context": p["context"],
        "tasks": draft["tasks"],
        "prerequisites": draft["prerequisites"],
        "external_processing_opt_in": True,
        "revision": p["revision"],
    }
    rev = api.put(f"/api/projects/{pid}", json=body, headers=PLANNER).json()["plan"]["project"][
        "revision"
    ]
    assert ask(api, pid, rev, step).status_code == 200 and fake.calls == 1


def prompt_payload(fake: FakeProvider) -> dict:
    return json.loads(fake.messages[1]["content"])


def accepted_project(api, headers, title: str) -> str:
    """A checkout project of `headers`' user with one accepted risk renamed to `title`."""
    res = api.post(
        "/api/projects",
        headers=headers,
        json=MIN
        | {
            "context": {"domain": "checkout"},
            "tasks": tasks_payload(),
            "prerequisites": prereqs_payload(),
        },
    )
    assert res.status_code == 201, res.text
    pid = res.json()["plan"]["project"]["id"]
    out = generate(api, pid, 1, headers=headers).json()
    graph = out["graph"]
    risk = graph["risks"][0]
    risk["title"], risk["review_status"] = title, "accepted"
    res = api.put(
        f"/api/projects/{pid}/graph",
        json={"graph": graph, "revision": out["revision"]},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return pid


def test_ai_learns_from_your_own_reviewed_risks_only(api, ai_on):
    accepted_project(api, PLANNER, "Planner lesson: pin the payment API version")
    accepted_project(api, REVIEWER, "Reviewer private lesson")  # someone else's project
    pid, rev, step = mapped_project(api)
    fake = ai_on(FakeProvider(json.dumps({"suggestions": []})))
    out = ask(api, pid, rev, step).json()
    sent = [e["title"] for e in prompt_payload(fake).get("learn_from", [])]
    assert "Planner lesson: pin the payment API version" in sent
    assert "Reviewer private lesson" not in json.dumps(fake.messages)
    used = [e["title"] for e in out["examples_used"]]
    assert "Planner lesson: pin the payment API version" in used
    assert "likelihood" not in json.dumps(prompt_payload(fake).get("learn_from", []))


def test_examples_can_be_switched_off(api, ai_on, monkeypatch):
    accepted_project(api, PLANNER, "Planner lesson")
    monkeypatch.setenv("PATHGUARD_AI_EXAMPLES", "0")
    monkeypatch.setenv("PATHGUARD_AI_AVOID", "0")
    reset_caches()
    pid, rev, step = mapped_project(api)
    fake = ai_on(FakeProvider(json.dumps({"suggestions": []})))
    out = ask(api, pid, rev, step).json()
    assert "learn_from" not in prompt_payload(fake) and out["examples_used"] == []


def test_remote_model_only_learns_from_opted_in_projects(api, ai_on, monkeypatch):
    accepted_project(api, PLANNER, "Not opted in lesson")
    monkeypatch.setenv("PATHGUARD_EXTERNAL_PROCESSING_ENABLED", "true")
    reset_caches()
    pid, rev, step = mapped_project(api)
    draft = api.get(f"/api/projects/{pid}", headers=PLANNER).json()["plan"]
    p = draft["project"]
    body = {
        "title": p["title"],
        "start": p["start"],
        "goal": p["goal"],
        "context": p["context"],
        "tasks": draft["tasks"],
        "prerequisites": draft["prerequisites"],
        "external_processing_opt_in": True,
        "revision": p["revision"],
    }
    rev = api.put(f"/api/projects/{pid}", json=body, headers=PLANNER).json()["plan"]["project"][
        "revision"
    ]
    fake = ai_on(FakeProvider(json.dumps({"suggestions": []}), remote=True))
    assert ask(api, pid, rev, step).status_code == 200
    assert "Not opted in lesson" not in json.dumps(fake.messages)


def test_ai_also_learns_from_the_risk_library(api, ai_on, monkeypatch):
    pid, rev, step = mapped_project(api)
    ai_on(FakeProvider(json.dumps({"suggestions": []})))
    out = ask(api, pid, rev, step).json()
    assert any(e["project_title"].startswith("Library: ") for e in out["examples_used"])

    monkeypatch.setenv("PATHGUARD_AI_USE_LIBRARY", "false")
    reset_caches()
    ai_on(
        FakeProvider(
            json.dumps(
                {
                    "suggestions": [
                        {
                            "type": "hazard",
                            "title": "x",
                            "affected_node_id": step,
                            "trigger": "t",
                            "consequence": "c",
                            "rationale": "r",
                        }
                    ]
                }
            )
        )
    )
    graph = api.get(f"/api/projects/{pid}/graph", headers=PLANNER).json()
    out = ask(api, pid, graph["revision"], step).json()
    assert not any(e["project_title"].startswith("Library: ") for e in out["examples_used"])
