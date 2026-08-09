"""Add section-level runbook search index.

Revision ID: 0003_runbook_sections
Revises: 0002_evidence_dedup
"""

from __future__ import annotations

from alembic import op

revision = "0003_runbook_sections"
down_revision = "0002_evidence_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE runbook_sections (
            runbook_id varchar(100) NOT NULL,
            version varchar(100) NOT NULL,
            section_id varchar(200) NOT NULL,
            title varchar(300) NOT NULL,
            parent_title varchar(300),
            content text NOT NULL,
            checksum varchar(64) NOT NULL,
            services jsonb NOT NULL,
            symptoms jsonb NOT NULL,
            search_vector tsvector GENERATED ALWAYS AS (
                to_tsvector(
                    'english'::regconfig,
                    coalesce(title, '') || ' ' ||
                    coalesce(content, '') || ' ' ||
                    coalesce(services::text, '') || ' ' ||
                    coalesce(symptoms::text, '')
                )
            ) STORED,
            embedding vector,
            PRIMARY KEY (runbook_id, version, section_id),
            FOREIGN KEY (runbook_id, version)
                REFERENCES runbook_versions(id, version)
        );
        CREATE INDEX ix_runbook_sections_search
            ON runbook_sections USING gin(search_vector);

        GRANT SELECT ON runbook_versions, runbook_sections
            TO incident_api_role, graph_worker_role, telemetry_mcp_role;
        """
    )


def downgrade() -> None:
    op.drop_table("runbook_sections")
