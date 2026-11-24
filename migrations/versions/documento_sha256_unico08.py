"""Documento original único por SHA-256

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # El mismo archivo no se registra dos veces, aunque cambie el número de factura
    # (p. ej. una factura sin número recibe cada vez un número provisional distinto).
    op.create_unique_constraint("uq_documento_sha256", "documento", ["sha256"])


def downgrade() -> None:
    op.drop_constraint("uq_documento_sha256", "documento", type_="unique")
