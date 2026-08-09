"""Persist promotion-cycle gate records without holdout contents.

Revision ID: 0011_promotion_gate
Revises: 0010_candidate_registry
"""

from __future__ import annotations

from alembic import op

revision = "0011_promotion_gate"
down_revision = "0010_candidate_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE promotion_cycles (
            id varchar(64) PRIMARY KEY,
            candidate_id varchar(64) NOT NULL REFERENCES candidate_versions(id),
            candidate_digest varchar(64) NOT NULL,
            status varchar(32) NOT NULL CHECK (
                status IN ('staging_frozen', 'holdout_passed', 'holdout_failed')
            ),
            holdout_suite_digest varchar(64),
            holdout_passed boolean,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE promotion_gate_records (
            id varchar(64) PRIMARY KEY,
            candidate_id varchar(64) NOT NULL REFERENCES candidate_versions(id),
            cycle_id varchar(64) REFERENCES promotion_cycles(id),
            status varchar(32) NOT NULL,
            decision_json jsonb NOT NULL,
            human_rejection_reason text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        REVOKE ALL ON promotion_cycles, promotion_gate_records
            FROM PUBLIC, graph_worker_role, telemetry_mcp_role, action_mcp_role, incident_api_role;
        GRANT SELECT, INSERT, UPDATE ON promotion_cycles, promotion_gate_records TO evaluation_role;
        """
    )


def downgrade() -> None:
    op.drop_table("promotion_gate_records")
    op.drop_table("promotion_cycles")
