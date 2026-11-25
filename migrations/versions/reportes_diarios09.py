"""Reportes de transacciones diarias (RF06) y su exportación (RF07)

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_TIPOS_ANTES = "tipo IN ('RECIBIDA', 'CLASIFICADA', 'APROBADA', 'CORREGIDA', 'RECHAZADA')"
_TIPOS_AHORA = "tipo IN ('RECIBIDA', 'CLASIFICADA', 'APROBADA', 'CORREGIDA', 'RECHAZADA', 'CONSOLIDADA', 'LIBERADA')"


def upgrade() -> None:
    op.create_table(
        "reporte_diario",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fecha_jornada", sa.Date, nullable=False),
        sa.Column("corte", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False),
        sa.Column("numero_transacciones", sa.Integer, nullable=False),
        sa.Column("total_debito", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_credito", sa.Numeric(18, 2), nullable=False),
        sa.Column("valor_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("movimientos", sa.JSON, nullable=False),
        sa.Column("subtotales", sa.JSON, nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("archivo_xml", sa.LargeBinary, nullable=True),
        sa.Column("archivo_sha256", sa.String(64), nullable=True),
        sa.Column("exportado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "exportado_por",
            sa.Integer,
            sa.ForeignKey("usuario.id", name="fk_reporte_diario_exportado_por"),
            nullable=True,
        ),
        sa.CheckConstraint("estado IN ('CONSOLIDADO', 'EXPORTADO', 'ANULADO')", name="ck_reporte_diario_estado"),
        sa.CheckConstraint(
            "(estado = 'EXPORTADO') = (archivo_xml IS NOT NULL AND archivo_sha256 IS NOT NULL)",
            name="ck_reporte_diario_exportado_con_archivo",
        ),
    )
    op.create_index("ix_reporte_diario_fecha_jornada", "reporte_diario", ["fecha_jornada"])

    # Vínculo histórico: sin cascada, se conserva aunque el reporte se anule (no repudio).
    op.create_table(
        "reporte_transaccion",
        sa.Column(
            "reporte_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reporte_diario.id", name="fk_reporte_transaccion_reporte"),
            primary_key=True,
        ),
        sa.Column(
            "transaccion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaccion.id", name="fk_reporte_transaccion_transaccion"),
            primary_key=True,
        ),
    )
    op.create_index("ix_reporte_transaccion_transaccion_id", "reporte_transaccion", ["transaccion_id"])

    op.create_table(
        "evento_reporte",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "reporte_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reporte_diario.id", name="fk_evento_reporte_reporte"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(12), nullable=False),
        # Nulo cuando lo hace el cierre programado.
        sa.Column("usuario_id", sa.Integer, sa.ForeignKey("usuario.id", name="fk_evento_reporte_usuario")),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("motivo", sa.Text, nullable=True),
        sa.Column("despues", sa.JSON, nullable=True),
        sa.CheckConstraint("tipo IN ('CONSOLIDADO', 'EXPORTADO', 'ANULADO')", name="ck_evento_reporte_tipo"),
    )
    op.create_index("ix_evento_reporte_reporte_id", "evento_reporte", ["reporte_id"])

    # Reporte vigente de cada transacción: uno como máximo.
    op.add_column(
        "transaccion",
        sa.Column(
            "reporte_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reporte_diario.id", name="fk_transaccion_reporte"),
            nullable=True,
        ),
    )
    op.create_index("ix_transaccion_reporte_id", "transaccion", ["reporte_id"])

    op.drop_constraint("ck_evento_transaccion_tipo", "evento_transaccion", type_="check")
    op.create_check_constraint("ck_evento_transaccion_tipo", "evento_transaccion", _TIPOS_AHORA)


def downgrade() -> None:
    op.drop_constraint("ck_evento_transaccion_tipo", "evento_transaccion", type_="check")
    op.create_check_constraint("ck_evento_transaccion_tipo", "evento_transaccion", _TIPOS_ANTES)
    op.drop_index("ix_transaccion_reporte_id", table_name="transaccion")
    op.drop_column("transaccion", "reporte_id")
    op.drop_index("ix_evento_reporte_reporte_id", table_name="evento_reporte")
    op.drop_table("evento_reporte")
    op.drop_index("ix_reporte_transaccion_transaccion_id", table_name="reporte_transaccion")
    op.drop_table("reporte_transaccion")
    op.drop_index("ix_reporte_diario_fecha_jornada", table_name="reporte_diario")
    op.drop_table("reporte_diario")
