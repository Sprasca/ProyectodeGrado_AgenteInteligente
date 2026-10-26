"""Esquema inicial: catálogo PUC, reglas, transacciones y partidas

Revision ID: 0001
Revises:
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "cuenta_puc",
        sa.Column("codigo", sa.String(10), primary_key=True),
        sa.Column("nombre", sa.String(200), nullable=False),
        sa.Column("naturaleza", sa.String(10), nullable=False),
    )
    op.create_table(
        "regla_clasificacion",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("codigo", sa.String(60), nullable=False, unique=True),
        sa.Column("cuenta_codigo", sa.String(10), sa.ForeignKey("cuenta_puc.codigo"), nullable=False),
        sa.Column("palabras_clave", postgresql.ARRAY(sa.String), nullable=False),
        sa.Column("descripcion", sa.Text, nullable=False, server_default=""),
        sa.Column("activa", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(384), nullable=True),
    )
    op.create_table(
        "transaccion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("numero_factura", sa.String(60), nullable=False, unique=True),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("nit_adquirente", sa.String(30), nullable=False),
        sa.Column("valor_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("confianza", sa.Numeric(4, 3), nullable=False),
        sa.Column("reglas_aplicadas", postgresql.ARRAY(sa.String), nullable=False),
        sa.Column("motivos_alerta", sa.JSON, nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "partida",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "transaccion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaccion.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cuenta_codigo", sa.String(10), sa.ForeignKey("cuenta_puc.codigo"), nullable=False),
        sa.Column("naturaleza", sa.String(10), nullable=False),
        sa.Column("valor", sa.Numeric(18, 2), nullable=False),
        sa.Column("descripcion", sa.String(200), nullable=False, server_default=""),
    )
    op.create_index("ix_partida_transaccion_id", "partida", ["transaccion_id"])


def downgrade() -> None:
    op.drop_table("partida")
    op.drop_table("transaccion")
    op.drop_table("regla_clasificacion")
    op.drop_table("cuenta_puc")
