"""Shared fixtures.

API tests run against a real PostgreSQL database (default: `pathguard_test`
on the Compose server at 127.0.0.1:26432). The schema is dropped and rebuilt
from migrations once per session, so this also exercises the migration from an
empty database. Engine tests need no database.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.db import get_sessionmaker, reset_caches
from app.seed import seed_demo_users

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB_URL = os.environ.get(
    "PATHGUARD_TEST_DATABASE_URL",
    "postgresql+psycopg://pathguard:pathguard@127.0.0.1:26432/pathguard_test",
)

PLANNER = {"X-PathGuard-Demo-User": "usr_demo0001"}
REVIEWER = {"X-PathGuard-Demo-User": "usr_demo0002"}
OUTSIDER = {"X-PathGuard-Demo-User": "usr_demo0003"}


def alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["configure_logger"] = False
    return cfg


def point_app_at(url: str) -> None:
    os.environ["PATHGUARD_DATABASE_URL"] = url
    reset_caches()


def rebuild_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    with get_sessionmaker()() as session:
        seed_demo_users(session)


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    url = make_url(TEST_DB_URL)
    assert url.database and url.database.endswith("_test"), "refusing to reset a non-test DB"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                 {"n": url.database})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError:
        pytest.skip(f"PostgreSQL not reachable at {url.host}:{url.port}; "
                    "run `docker compose up -d db` first")
    finally:
        admin.dispose()

    previous = os.environ.get("PATHGUARD_DATABASE_URL")
    point_app_at(TEST_DB_URL)
    engine = create_engine(TEST_DB_URL)
    rebuild_schema(engine)
    yield engine
    engine.dispose()
    if previous is None:
        os.environ.pop("PATHGUARD_DATABASE_URL", None)
    else:
        os.environ["PATHGUARD_DATABASE_URL"] = previous
    reset_caches()


@pytest.fixture
def api(pg_engine: Engine) -> Iterator[TestClient]:
    point_app_at(TEST_DB_URL)
    with pg_engine.begin() as conn:
        conn.execute(text("TRUNCATE projects, audit_events RESTART IDENTITY CASCADE"))
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
