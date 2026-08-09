"""Add deterministic Evidence identity."""

from alembic import op

revision = "0002_evidence_dedup"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_evidence_identity",
        "evidence",
        ["incident_id", "kind", "digest"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_evidence_identity", "evidence", type_="unique")
