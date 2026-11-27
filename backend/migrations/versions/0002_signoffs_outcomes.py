"""Report sign-offs and observed outcomes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26

Both tables are insert-only (a trigger rejects UPDATE). Rows go away only when
their project is deleted (ON DELETE CASCADE), so DELETE is allowed.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ID = sa.String(48)
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "report_signoffs",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("version_id", ID, sa.ForeignKey("scenario_versions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("reviewer_id", ID, nullable=False),
        sa.Column("reviewer_name", sa.String(200), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )
    op.create_table(
        "observed_outcomes",
        sa.Column("id", ID, primary_key=True),
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("version_id", ID, sa.ForeignKey("scenario_versions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("decision_group_id", ID, nullable=False),
        sa.Column("transition_id", ID),
        sa.Column("observed_on", sa.Date, nullable=False),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date),
        sa.Column("exposure", sa.Integer, nullable=False),
        sa.Column("occurrences", sa.Integer, nullable=False),
        sa.Column("context", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.Text),
        sa.Column("recorded_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("exposure > 0", name="observed_outcomes_exposure"),
        sa.CheckConstraint("occurrences >= 0 AND occurrences <= exposure",
                           name="observed_outcomes_occurrences"),
    )
    for table in ("report_signoffs", "observed_outcomes"):
        op.execute(f"""
            CREATE TRIGGER {table}_insert_only BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION pathguard_reject_change();
        """)


def downgrade() -> None:
    op.drop_table("observed_outcomes")
    op.drop_table("report_signoffs")
