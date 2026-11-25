"""Notificaciones (RF04): bandeja por usuario, envío por correo y correo de cada usuario

- notificacion: una por causa; índice único parcial (tipo, clave) entre las no atendidas.
- notificacion_usuario: la copia de cada destinatario, con su estado de lectura.
- envio_correo: el envío opcional por correo, con estado, intentos y último error.
- usuario.email (opcional).
- alerta_tecnica (Fase 4) se integra: sus filas pasan a notificaciones JORNADA_SIN_CIERRE para los ADMIN
  activos (atendidas si la jornada ya tiene cierre vigente) y la tabla se elimina.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_TIPOS = (
    "tipo IN ('TRANSACCION_EN_ALERTA', 'CONSOLIDACION_BLOQUEADA', 'REPORTE_DISPONIBLE', 'FACTURA_DUPLICADA', "
    "'DESCUADRE', 'EXPORTACION_FALLIDA', 'JORNADA_SIN_CIERRE', 'ARCHIVO_NO_RECIBIDO', 'DIRECTORIO_NO_DISPONIBLE', "
    "'ERROR_RECEPCION', 'CORREO_NO_ENVIADO')"
)


def upgrade() -> None:
    op.add_column("usuario", sa.Column("email", sa.String(254), nullable=True))

    op.create_table(
        "notificacion",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("clase", sa.String(8), nullable=False),
        sa.Column("clave", sa.String(300), nullable=False),
        sa.Column("mensaje", sa.Text, nullable=False),
        sa.Column("datos", sa.JSON, nullable=True),
        sa.Column(
            "transaccion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transaccion.id", name="fk_notificacion_transaccion"),
            nullable=True,
        ),
        sa.Column(
            "reporte_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reporte_diario.id", name="fk_notificacion_reporte"),
            nullable=True,
        ),
        sa.Column("fecha_jornada", sa.Date, nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("atendida_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "atendida_por", sa.Integer, sa.ForeignKey("usuario.id", name="fk_notificacion_atendida_por"), nullable=True
        ),
        sa.Column("resolucion", sa.Text, nullable=True),
        sa.CheckConstraint(_TIPOS, name="ck_notificacion_tipo"),
        sa.CheckConstraint("clase IN ('CONTABLE', 'TECNICA')", name="ck_notificacion_clase"),
    )
    op.create_index("ix_notificacion_transaccion_id", "notificacion", ["transaccion_id"])
    op.create_index("ix_notificacion_reporte_id", "notificacion", ["reporte_id"])
    op.create_index(
        "uq_notificacion_causa_abierta",
        "notificacion",
        ["tipo", "clave"],
        unique=True,
        postgresql_where=sa.text("atendida_en IS NULL"),
    )

    op.create_table(
        "notificacion_usuario",
        sa.Column(
            "notificacion_id",
            sa.Integer,
            sa.ForeignKey("notificacion.id", name="fk_notificacion_usuario_notificacion"),
            primary_key=True,
        ),
        sa.Column(
            "usuario_id",
            sa.Integer,
            sa.ForeignKey("usuario.id", name="fk_notificacion_usuario_usuario"),
            primary_key=True,
        ),
        sa.Column("informativa", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("leida_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notificacion_usuario_usuario_id", "notificacion_usuario", ["usuario_id"])

    op.create_table(
        "envio_correo",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "notificacion_id",
            sa.Integer,
            sa.ForeignKey("notificacion.id", name="fk_envio_correo_notificacion"),
            nullable=False,
        ),
        sa.Column(
            "usuario_id", sa.Integer, sa.ForeignKey("usuario.id", name="fk_envio_correo_usuario"), nullable=False
        ),
        sa.Column("destinatario", sa.String(254), nullable=False),
        sa.Column("estado", sa.String(9), nullable=False),
        sa.Column("intentos", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("ultimo_error", sa.Text, nullable=True),
        sa.Column("proximo_intento", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("enviado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("estado IN ('PENDIENTE', 'ENVIADA', 'FALLIDA')", name="ck_envio_correo_estado"),
        sa.UniqueConstraint("notificacion_id", "usuario_id", name="uq_envio_correo_notificacion_usuario"),
    )
    op.create_index("ix_envio_correo_pendientes", "envio_correo", ["estado", "proximo_intento"])

    # alerta_tecnica → notificaciones JORNADA_SIN_CIERRE (atendidas si la jornada ya tiene cierre vigente).
    op.execute(
        """
        INSERT INTO notificacion (tipo, clase, clave, mensaje, datos, fecha_jornada, creada_en, actualizada_en,
                                  atendida_en, resolucion)
        SELECT 'JORNADA_SIN_CIERRE', 'TECNICA', 'jornada:' || a.fecha_jornada, a.mensaje, a.datos, a.fecha_jornada,
               a.creado_en, a.creado_en,
               CASE WHEN c.id IS NOT NULL THEN now() END,
               CASE WHEN c.id IS NOT NULL THEN 'La jornada ya tiene reporte de cierre.' END
        FROM alerta_tecnica a
        LEFT JOIN reporte_diario c
               ON c.fecha_jornada = a.fecha_jornada AND c.tipo = 'CIERRE' AND c.estado <> 'ANULADO'
        WHERE a.tipo = 'JORNADA_SIN_CIERRE'
        """
    )
    op.execute(
        """
        INSERT INTO notificacion_usuario (notificacion_id, usuario_id)
        SELECT n.id, u.id FROM notificacion n CROSS JOIN usuario u
        WHERE n.tipo = 'JORNADA_SIN_CIERRE' AND u.rol = 'ADMIN' AND u.activo
        """
    )
    op.drop_table("alerta_tecnica")


def downgrade() -> None:
    op.create_table(
        "alerta_tecnica",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("fecha_jornada", sa.Date, nullable=True),
        sa.Column("mensaje", sa.Text, nullable=False),
        sa.Column("datos", sa.JSON, nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("notificada_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("tipo IN ('JORNADA_SIN_CIERRE')", name="ck_alerta_tecnica_tipo"),
        sa.UniqueConstraint("tipo", "fecha_jornada", name="uq_alerta_tecnica_tipo_jornada"),
    )
    # La más antigua de cada jornada vuelve a alerta_tecnica (la constancia del intento del cierre programado).
    op.execute(
        """
        INSERT INTO alerta_tecnica (tipo, fecha_jornada, mensaje, datos, creado_en)
        SELECT DISTINCT ON (fecha_jornada) 'JORNADA_SIN_CIERRE', fecha_jornada, mensaje, datos, creada_en
        FROM notificacion WHERE tipo = 'JORNADA_SIN_CIERRE'
        ORDER BY fecha_jornada, creada_en
        """
    )
    op.drop_index("ix_envio_correo_pendientes", table_name="envio_correo")
    op.drop_table("envio_correo")
    op.drop_index("ix_notificacion_usuario_usuario_id", table_name="notificacion_usuario")
    op.drop_table("notificacion_usuario")
    op.drop_index("uq_notificacion_causa_abierta", table_name="notificacion")
    op.drop_index("ix_notificacion_reporte_id", table_name="notificacion")
    op.drop_index("ix_notificacion_transaccion_id", table_name="notificacion")
    op.drop_table("notificacion")
    op.drop_column("usuario", "email")
