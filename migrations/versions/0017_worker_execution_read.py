"""Allow the graph worker to resume after a persisted action result.

Revision ID: 0017_worker_execution_read
Revises: 0016_worker_proposal_grant
"""

from alembic import op

revision = "0017_worker_execution_read"
down_revision = "0016_worker_proposal_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON action_executions, verification_results TO graph_worker_role")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON action_executions, verification_results FROM graph_worker_role")
