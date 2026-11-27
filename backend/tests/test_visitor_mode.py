"""Public-demo hosting: per-browser visitor identity, isolation, limits, expiry, static app."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import VISITOR_COOKIE
from app.config import Settings
from app.db import reset_caches

SECRET = "x" * 40
MIN = {"start": "Checkout change approved", "goal": "Reliable launch"}


@pytest.fixture
def visitor_app(api, monkeypatch):
    """App factory in visitor mode (the `api` fixture prepares the test database)."""
    monkeypatch.setenv("PATHGUARD_DEMO_IDENTITY_ENABLED", "false")
    monkeypatch.setenv("PATHGUARD_VISITOR_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("PATHGUARD_SECRET_KEY", SECRET)
    reset_caches()
    from app.main import create_app

    clients: list[TestClient] = []

    def make() -> TestClient:
        client = TestClient(create_app())
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for c in clients:
        c.__exit__(None, None, None)
    monkeypatch.undo()
    reset_caches()


@pytest.fixture
def browser(visitor_app) -> Iterator[TestClient]:
    yield visitor_app()


def test_each_browser_gets_a_stable_visitor_identity(browser):
    first = browser.get("/api/me")
    assert first.status_code == 200
    assert VISITOR_COOKIE in first.cookies or VISITOR_COOKIE in browser.cookies
    me = first.json()
    assert me["id"].startswith("usr_") and me["display_name"] == "Visitor"
    assert browser.get("/api/me").json()["id"] == me["id"]


def test_visitors_only_see_their_own_projects(visitor_app):
    alice, bob = visitor_app(), visitor_app()
    pid = alice.post("/api/projects", json=MIN).json()["plan"]["project"]["id"]
    assert [p["id"] for p in alice.get("/api/projects").json()] == [pid]
    assert bob.get("/api/projects").json() == []
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert bob.delete(f"/api/projects/{pid}").status_code == 404


def test_tampered_cookie_gets_a_new_identity(browser):
    me = browser.get("/api/me").json()["id"]
    browser.cookies.clear()
    browser.cookies.set(VISITOR_COOKIE, f"{me}.forged")
    assert browser.get("/api/me").json()["id"] != me
    browser.cookies.clear()
    browser.cookies.set(VISITOR_COOKIE, "usr_demo0001.forged")
    assert browser.get("/api/me").json()["id"] != "usr_demo0001"


def test_demo_header_is_ignored_in_visitor_mode(browser):
    me = browser.get("/api/me", headers={"X-PathGuard-Demo-User": "usr_demo0001"}).json()
    assert me["id"] != "usr_demo0001"


def test_project_limit_per_browser(browser, monkeypatch):
    monkeypatch.setenv("PATHGUARD_VISITOR_PROJECT_LIMIT", "2")
    reset_caches()
    for _ in range(2):
        assert browser.post("/api/projects", json=MIN).status_code == 201
    res = browser.post("/api/projects", json=MIN)
    assert res.status_code == 429 and res.json()["detail"]["code"] == "project_limit"


def test_untouched_projects_expire(browser, pg_engine):
    pid = browser.post("/api/projects", json=MIN).json()["plan"]["project"]["id"]
    with pg_engine.begin() as conn:
        conn.execute(text("UPDATE projects SET updated_at = now() - interval '8 days' "
                          "WHERE id = :id"), {"id": pid})
    from app.purge import main as purge

    purge()
    assert browser.get(f"/api/projects/{pid}").status_code == 404
    assert browser.get("/api/projects").json() == []


def test_health_reports_visitor_mode(browser):
    body = browser.get("/api/health").json()
    assert body["identity"] == "visitor" and body["visitor_retention_days"] == 7


def test_oversized_body_is_rejected(browser):
    res = browser.post("/api/projects", content=b"x" * 2_000_001,
                       headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_static_app_is_served_beside_the_api(api, monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("<title>PathGuard</title>", encoding="utf-8")
    monkeypatch.setenv("PATHGUARD_STATIC_DIR", str(tmp_path))
    reset_caches()
    from app.main import create_app

    with TestClient(create_app()) as client:
        assert "PathGuard" in client.get("/").text
        assert client.get("/api/health").json()["status"] == "ok"
    monkeypatch.undo()
    reset_caches()


def test_settings_guards():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(demo_identity_enabled=False, visitor_identity_enabled=True, secret_key="short")
    with pytest.raises(ValueError, match="not both"):
        Settings(demo_identity_enabled=True, visitor_identity_enabled=True, secret_key=SECRET)
    s = Settings(database_url="postgres://u:p@host/db?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"
