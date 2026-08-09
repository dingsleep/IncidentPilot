"""Allow heartbeat upserts to read their conflict target.

Revision ID: 0014_telemetry_heartbeat_select
Revises: 0013_telemetry_heartbeat_grant
"""

from alembic import op

revision = "0014_telemetry_heartbeat_select"
down_revision = "0013_telemetry_heartbeat_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT ON service_heartbeats TO telemetry_mcp_role")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON service_heartbeats FROM telemetry_mcp_role")
