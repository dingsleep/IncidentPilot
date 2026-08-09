"""Persist immutable evolution candidates and one active prompt per agent.

Revision ID: 0010_candidate_registry
Revises: 0009_evolution_tables
"""

from __future__ import annotations

from alembic import op

revision = "0010_candidate_registry"
down_revision = "0009_evolution_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE candidate_versions (
            id varchar(64) PRIMARY KEY,
            kind varchar(32) NOT NULL CHECK (
                kind IN ('prompt', 'tool_description', 'runbook_draft')
            ),
            base_version varchar(100) NOT NULL,
            artifact_uri varchar(300) NOT NULL,
            artifact_json jsonb NOT NULL,
            diff text NOT NULL,
            target_failure_label varchar(64) NOT NULL,
            target_component varchar(100) NOT NULL,
            generator_model varchar(100) NOT NULL,
            digest varchar(64) NOT NULL UNIQUE,
            status varchar(32) NOT NULL CHECK (
                status IN ('candidate', 'staging', 'rejected', 'approved')
            ),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE UNIQUE INDEX ux_prompt_versions_one_active
            ON prompt_versions (agent_name) WHERE status = 'active';
        REVOKE ALL ON candidate_versions
            FROM PUBLIC, graph_worker_role, telemetry_mcp_role, action_mcp_role, incident_api_role;
        GRANT SELECT, INSERT, UPDATE ON candidate_versions TO evaluation_role;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_prompt_versions_one_active")
    op.drop_table("candidate_versions")
