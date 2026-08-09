"""Allow the evaluation role to read immutable scoring facts.

Revision ID: 0007_evaluation_fact_grants
Revises: 0006_evaluation_tables
"""

from __future__ import annotations

from alembic import op

revision = "0007_evaluation_fact_grants"
down_revision = "0006_evaluation_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT ON diagnoses, tool_calls, model_calls, action_proposals, "
        "approvals, action_executions, verification_results TO evaluation_role"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT ON diagnoses, tool_calls, model_calls, action_proposals, "
        "approvals, action_executions, verification_results FROM evaluation_role"
    )
