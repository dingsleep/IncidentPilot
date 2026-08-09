"""Allow the incident API to read evolution records.

Revision ID: 0012_evolution_api_read
Revises: 0011_promotion_gate
"""

from alembic import op

revision = "0012_evolution_api_read"
down_revision = "0011_promotion_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT ON candidate_versions, promotion_cycles, promotion_gate_records "
        "TO incident_api_role"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE ALL ON candidate_versions, promotion_cycles, promotion_gate_records "
        "FROM incident_api_role"
    )
