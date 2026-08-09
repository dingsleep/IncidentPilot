"""Add evaluation run and case tables with isolated role grants.

Revision ID: 0006_evaluation_tables
Revises: 0005_worker_timeline_grant
"""

from __future__ import annotations

from alembic import op

revision = "0006_evaluation_tables"
down_revision = "0005_worker_timeline_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evaluation_runs (
            id varchar(64) PRIMARY KEY,
            suite_version varchar(100) NOT NULL,
            candidate_version varchar(100) NOT NULL,
            status varchar(32) NOT NULL,
            aggregate_metrics jsonb NOT NULL
        );
        CREATE TABLE evaluation_cases (
            id varchar(64) PRIMARY KEY,
            run_id varchar(64) NOT NULL REFERENCES evaluation_runs(id),
            scenario_id varchar(200) NOT NULL,
            metrics jsonb NOT NULL,
            hard_failures jsonb NOT NULL,
            CONSTRAINT uq_evaluation_case_scenario UNIQUE (run_id, scenario_id)
        );

        REVOKE ALL ON evaluation_runs, evaluation_cases
            FROM PUBLIC, graph_worker_role, telemetry_mcp_role, action_mcp_role;
        GRANT SELECT, INSERT, UPDATE ON evaluation_runs, evaluation_cases
            TO evaluation_role;
        GRANT SELECT ON evaluation_runs, evaluation_cases TO incident_api_role;
        """
    )


def downgrade() -> None:
    op.drop_table("evaluation_cases")
    op.drop_table("evaluation_runs")
