"""Add sanitized offline evolution trajectory storage.

Revision ID: 0009_evolution_tables
Revises: 0008_action_mcp_nonce_grant
"""

from __future__ import annotations

from alembic import op

revision = "0009_evolution_tables"
down_revision = "0008_action_mcp_nonce_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evolution_trajectories (
            id varchar(64) PRIMARY KEY,
            run_id varchar(64) NOT NULL REFERENCES evaluation_runs(id),
            scenario_id varchar(200) NOT NULL,
            split varchar(16) NOT NULL CHECK (split IN ('train', 'validation')),
            provenance_json jsonb NOT NULL,
            payload_json jsonb NOT NULL,
            quality_reasons jsonb NOT NULL,
            content_digest varchar(64) NOT NULL,
            digest varchar(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT uq_evolution_trajectory_digest UNIQUE (digest)
        );
        REVOKE ALL ON evolution_trajectories
            FROM PUBLIC, graph_worker_role, telemetry_mcp_role, action_mcp_role, incident_api_role;
        GRANT SELECT, INSERT, UPDATE ON evolution_trajectories TO evaluation_role;
        """
    )


def downgrade() -> None:
    op.drop_table("evolution_trajectories")
