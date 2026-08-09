"""Allow the read-only telemetry process to publish only its own heartbeat.

Revision ID: 0013_telemetry_heartbeat_grant
Revises: 0012_evolution_api_read
"""

from alembic import op

revision = "0013_telemetry_heartbeat_grant"
down_revision = "0012_evolution_api_read"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT INSERT, UPDATE ON service_heartbeats TO telemetry_mcp_role")


def downgrade() -> None:
    op.execute("REVOKE INSERT, UPDATE ON service_heartbeats FROM telemetry_mcp_role")
