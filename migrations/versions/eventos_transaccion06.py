"""Trazabilidad: tabla evento_transaccion

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evento_transaccion",
        sa.Column("id", sa.Integer, primary_key=True),
        # Sin ON DELETE CASCADE: la evidencia no desaparece al borrar una transacción (no repudio).
        sa.Column(
            "transaccion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaccion.id", name="fk_evento_transaccion_transaccion"),
            nullable=False,
        ),
        sa.Column("tipo", sa.String(12), nullable=False),
        # Nulo en los eventos automáticos (p. ej. CLASIFICADA).
        sa.Column("usuario_id", sa.Integer, sa.ForeignKey("usuario.id", name="fk_evento_transaccion_usuario")),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("motivo", sa.Text, nullable=True),
        sa.Column("antes", sa.JSON, nullable=True),
        sa.Column("despues", sa.JSON, nullable=True),
        sa.CheckConstraint(
            "tipo IN ('RECIBIDA', 'CLASIFICADA', 'APROBADA', 'CORREGIDA', 'RECHAZADA')",
            name="ck_evento_transaccion_tipo",
        ),
    )
    op.create_index("ix_evento_transaccion_transaccion_id", "evento_transaccion", ["transaccion_id"])


def downgrade() -> None:
    op.drop_index("ix_evento_transaccion_transaccion_id", table_name="evento_transaccion")
    op.drop_table("evento_transaccion")
