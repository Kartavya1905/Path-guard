"""Initial schema: users, projects, members, tasks, prerequisites, versions, audit.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ID = sa.String(48)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", ID, primary_key=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("is_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "projects",
        sa.Column("id", ID, primary_key=True),
        sa.Column("owner_id", ID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(2000), nullable=False),
        sa.Column("start", sa.Text, nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("success_criteria", sa.Text),
        sa.Column("context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("external_processing_opt_in", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("graph", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("length(btrim(start)) > 0", name="projects_start_required"),
        sa.CheckConstraint("length(btrim(goal)) > 0", name="projects_goal_required"),
    )
    op.create_table(
        "project_members",
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", ID, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role in ('owner','editor','reviewer','viewer')",
                           name="project_members_role"),
    )
    op.create_table(
        "tasks",
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("id", ID, primary_key=True),
        sa.Column("title", sa.String(2000), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("owner", sa.String(2000)),
        sa.Column("duration_days", sa.Numeric(10, 2)),
        sa.Column("cost", sa.Numeric(14, 2)),
    )
    op.create_table(
        "prerequisites",
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("id", ID, primary_key=True),
        sa.Column("before_task_id", ID, nullable=False),
        sa.Column("after_task_id", ID, nullable=False),
        sa.ForeignKeyConstraint(["project_id", "before_task_id"],
                                ["tasks.project_id", "tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id", "after_task_id"],
                                ["tasks.project_id", "tasks.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "scenario_versions",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("label", sa.String(2000), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_version_id", ID, sa.ForeignKey("scenario_versions.id")),
        sa.Column("created_by", ID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("project_id", "number"),
        sa.CheckConstraint("kind in ('baseline','variant','snapshot')",
                           name="scenario_versions_kind"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("project_id", ID, index=True),
        sa.Column("actor_id", ID, nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_kind", sa.String(32)),
        sa.Column("target_id", ID),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.execute("""
        CREATE FUNCTION pathguard_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER scenario_versions_immutable BEFORE UPDATE ON scenario_versions
        FOR EACH ROW EXECUTE FUNCTION pathguard_reject_change();
    """)
    op.execute("""
        CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION pathguard_reject_change();
    """)


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("scenario_versions")
    op.drop_table("prerequisites")
    op.drop_table("tasks")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
    op.execute("DROP FUNCTION IF EXISTS pathguard_reject_change()")
