"""Auditoría de reglas: fechas en regla_clasificacion y tabla historial_regla

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ACCIONES = ("CREAR", "EDITAR", "ACTIVAR", "DESACTIVAR")


def upgrade() -> None:
    op.add_column(
        "regla_clasificacion",
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "regla_clasificacion",
        sa.Column("actualizado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "historial_regla",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "regla_codigo",
            sa.String(60),
            sa.ForeignKey("regla_clasificacion.codigo", name="fk_historial_regla_codigo"),
            nullable=False,
        ),
        sa.Column("accion", sa.String(12), nullable=False),
        sa.Column("antes", sa.JSON, nullable=True),
        sa.Column("despues", sa.JSON, nullable=True),
        # Sin autenticación hasta la Fase 2: puede quedar vacío.
        sa.Column("usuario", sa.Text, nullable=True),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "accion IN (" + ", ".join(f"'{a}'" for a in ACCIONES) + ")", name="ck_historial_regla_accion"
        ),
    )
    op.create_index("ix_historial_regla_regla_codigo", "historial_regla", ["regla_codigo"])


def downgrade() -> None:
    op.drop_index("ix_historial_regla_regla_codigo", table_name="historial_regla")
    op.drop_table("historial_regla")
    op.drop_column("regla_clasificacion", "actualizado_en")
    op.drop_column("regla_clasificacion", "creado_en")
