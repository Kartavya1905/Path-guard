"""Cache of optional AI suggestion replies.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26

One row per (project, cache key). The key covers the prompt version, provider,
model and the exact input, so a matching request reuses the stored reply instead
of calling the model again.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

ID = sa.String(48)


def upgrade() -> None:
    op.create_table(
        "ai_suggestion_cache",
        sa.Column("project_id", ID, sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("ai_suggestion_cache")
