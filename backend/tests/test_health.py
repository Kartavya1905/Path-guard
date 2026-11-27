import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.db import reset_caches


@pytest.fixture
def client_for(monkeypatch):
    def make(database_url: str) -> TestClient:
        monkeypatch.setenv("PATHGUARD_DATABASE_URL", database_url)
        reset_caches()
        from app.main import create_app

        return TestClient(create_app())

    yield make
    monkeypatch.undo()
    reset_caches()


def test_health_ok_when_database_answers(client_for):
    res = client_for("sqlite://").get("/api/health")
    assert res.status_code == 200
    assert res.json()["database"] == "ok"
    assert res.json()["external_processing"] is False


def test_health_503_when_database_down(client_for):
    res = client_for("sqlite:///Z:/definitely/missing/dir/x.db").get("/api/health")
    assert res.status_code == 503
    assert res.json()["status"] == "degraded"


def test_production_rejects_demo_identity():
    with pytest.raises(ValidationError):
        Settings(env="production", demo_identity_enabled=True)
    assert Settings(env="production", demo_identity_enabled=False).env == "production"
