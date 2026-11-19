"""Conserva el asiento que propuso el agente al corregir una transacción

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transaccion", sa.Column("asiento_propuesto", sa.JSON, nullable=True))
    op.add_column("transaccion", sa.Column("corregido_en", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("transaccion", "corregido_en")
    op.drop_column("transaccion", "asiento_propuesto")
