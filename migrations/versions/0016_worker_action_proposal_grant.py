"""Allow the graph worker to persist bounded action proposals.

Revision ID: 0016_worker_proposal_grant
Revises: 0015_online_remediation_grants
"""

from alembic import op

revision = "0016_worker_proposal_grant"
down_revision = "0015_online_remediation_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT INSERT, UPDATE ON action_proposals TO graph_worker_role")


def downgrade() -> None:
    op.execute("REVOKE INSERT, UPDATE ON action_proposals FROM graph_worker_role")
