"""Record the active prompt version on every model call.

Revision ID: 0004_model_call_prompt_version
Revises: 0003_runbook_sections
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_model_call_prompt_version"
down_revision = "0003_runbook_sections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_calls",
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
    )
    op.execute(
        "UPDATE model_calls SET prompt_version = 'legacy-unknown' WHERE prompt_version IS NULL"
    )
    op.alter_column("model_calls", "prompt_version", nullable=False)


def downgrade() -> None:
    op.drop_column("model_calls", "prompt_version")
