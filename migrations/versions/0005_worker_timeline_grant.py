"""Allow the graph worker to append auditable timeline events.

Revision ID: 0005_worker_timeline_grant
Revises: 0004_model_call_prompt_version
"""

from __future__ import annotations

from alembic import op

revision = "0005_worker_timeline_grant"
down_revision = "0004_model_call_prompt_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT ON audit_events TO graph_worker_role")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON audit_events FROM graph_worker_role")
