"""Allow Action MCP to consume an approval nonce exactly once.

Revision ID: 0008_action_mcp_nonce_grant
Revises: 0007_evaluation_fact_grants
"""

from __future__ import annotations

from alembic import op

revision = "0008_action_mcp_nonce_grant"
down_revision = "0007_evaluation_fact_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT UPDATE (nonce_used_at) ON approvals TO action_mcp_role")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (nonce_used_at) ON approvals FROM action_mcp_role")
