"""Origen de las reglas (csv / contador) y tabla usuario

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

    op.create_table(
        "usuario",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("nombre_usuario", sa.String(60), nullable=False, unique=True),
        sa.Column("nombre_completo", sa.String(200), nullable=False),
        sa.Column("hash_contrasena", sa.Text, nullable=False),  # Argon2id
        sa.Column("rol", sa.String(10), nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rol IN ('CONTADOR', 'AUXILIAR', 'ADMIN')", name="ck_usuario_rol"),
    )


def downgrade() -> None:
    op.drop_table("usuario")
    op.drop_constraint("ck_regla_clasificacion_origen", "regla_clasificacion", type_="check")
    op.drop_column("regla_clasificacion", "origen")
