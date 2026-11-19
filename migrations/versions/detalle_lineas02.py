"""Detalle por línea de la clasificación en transaccion

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transaccion",
        sa.Column("detalle_lineas", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("transaccion", "detalle_lineas")
