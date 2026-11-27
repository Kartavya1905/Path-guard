from alembic import command
from sqlalchemy import inspect

from tests.conftest import alembic_config, rebuild_schema

TABLES = {"users", "projects", "project_members", "tasks", "prerequisites",
          "scenario_versions", "audit_events", "alembic_version"}


def test_migrations_from_empty_database_and_back(pg_engine):
    rebuild_schema(pg_engine)  # drop everything, upgrade head from empty
    assert TABLES <= set(inspect(pg_engine).get_table_names())

    command.downgrade(alembic_config(), "base")
    assert set(inspect(pg_engine).get_table_names()) <= {"alembic_version"}

    rebuild_schema(pg_engine)  # leave a migrated, seeded schema for other tests
    assert TABLES <= set(inspect(pg_engine).get_table_names())
