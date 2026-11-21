"""Origen de las reglas (csv / contador)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Todas las reglas que ya existen vienen de la siembra de data/puc: quedan con "csv".
    op.add_column(
        "regla_clasificacion",
        sa.Column("origen", sa.String(10), server_default=sa.text("'csv'"), nullable=False),
    )
    op.create_check_constraint("ck_regla_clasificacion_origen", "regla_clasificacion", "origen IN ('csv', 'contador')")


def downgrade() -> None:
    op.drop_constraint("ck_regla_clasificacion_origen", "regla_clasificacion", type_="check")
    op.drop_column("regla_clasificacion", "origen")
