"""Grant the bounded online remediation writers their required tables.

Revision ID: 0015_online_remediation_grants
Revises: 0014_telemetry_heartbeat_select
"""

from alembic import op

revision = "0015_online_remediation_grants"
down_revision = "0014_telemetry_heartbeat_select"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT INSERT ON verification_results TO graph_worker_role")
    op.execute("GRANT SELECT, INSERT, UPDATE ON service_heartbeats TO action_mcp_role")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON verification_results FROM graph_worker_role")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON service_heartbeats FROM action_mcp_role")
