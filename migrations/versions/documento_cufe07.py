"""Documento original de cada transacción y CUFE único

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CUFE: cbc:UUID de la factura DIAN (SHA-384 en hexadecimal, 96 caracteres). Único; nulo si no viene.
    op.add_column("transaccion", sa.Column("cufe", sa.String(128), nullable=True))
    op.create_unique_constraint("uq_transaccion_cufe", "transaccion", ["cufe"])

    op.create_table(
        "documento",
        # Un documento por transacción. Sin ON DELETE CASCADE: el original no se pierde (no repudio).
        sa.Column(
            "transaccion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaccion.id", name="fk_documento_transaccion"),
            primary_key=True,
        ),
        sa.Column("nombre_archivo", sa.String(255), nullable=False),
        sa.Column("tipo", sa.String(3), nullable=False),
        sa.Column("contenido", sa.LargeBinary, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("recibido_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("tipo IN ('XML', 'PDF')", name="ck_documento_tipo"),
    )


def downgrade() -> None:
    op.drop_table("documento")
    op.drop_constraint("uq_transaccion_cufe", "transaccion", type_="unique")
    op.drop_column("transaccion", "cufe")
