"""Horario de la jornada en la base, jornada de cada transacción, reportes parciales y de cierre, alertas técnicas

- horario_jornada: el horario que define el contador (inicial 08:00 a 18:00), con quién lo cambió y cuándo.
- transaccion.fecha_jornada: la jornada de cada transacción. Las existentes se calculan con el corte de las
  18:00 de Colombia: antes de esa hora, la del mismo día; después, la del día siguiente.
- reporte_diario: tipo (PARCIAL o CIERRE), número consecutivo en la jornada y cupo (1 a 4 los parciales, 5
  el cierre). Un cupo no se repite entre los reportes vigentes de una jornada: máximo 4 parciales y un cierre.
  Los reportes que ya existían quedan como PARCIALES numerados por fecha de creación.
- alerta_tecnica: la jornada que quedó sin reporte de cierre (la Fase 5 la convertirá en notificación).
- Evento de transacción EXPORTADA.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_TIPOS_ANTES = "tipo IN ('RECIBIDA', 'CLASIFICADA', 'APROBADA', 'CORREGIDA', 'RECHAZADA', 'CONSOLIDADA', 'LIBERADA')"
_TIPOS_AHORA = (
    "tipo IN ('RECIBIDA', 'CLASIFICADA', 'APROBADA', 'CORREGIDA', 'RECHAZADA', 'CONSOLIDADA', 'LIBERADA', "
    "'EXPORTADA')"
)
_LOCAL = "(creado_en AT TIME ZONE 'America/Bogota')"
_MAXIMO_PARCIALES = 4


def _verificar_reportes_existentes() -> None:
    """Si una jornada ya tiene más de 4 reportes vigentes no caben en los cupos: se detiene sin tocar nada."""
    excedidas = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT fecha_jornada, count(*) FROM reporte_diario WHERE estado <> 'ANULADO' "
                "GROUP BY fecha_jornada HAVING count(*) > :maximo ORDER BY fecha_jornada"
            ),
            {"maximo": _MAXIMO_PARCIALES},
        )
        .all()
    )
    if excedidas:
        detalle = ", ".join(f"{fecha} ({n} reportes)" for fecha, n in excedidas)
        raise RuntimeError(
            f"No se puede aplicar la migración 0010: estas jornadas tienen más de {_MAXIMO_PARCIALES} reportes "
            f"vigentes, que no caben en los cupos de reportes parciales: {detalle}. Anule los sobrantes "
            "(POST /reportes/{id}/anular) y vuelva a ejecutar alembic upgrade head."
        )


def upgrade() -> None:
    _verificar_reportes_existentes()

    op.create_table(
        "horario_jornada",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("hora_apertura", sa.Time, nullable=False),
        sa.Column("hora_cierre", sa.Time, nullable=False),
        sa.Column("usuario_id", sa.Integer, sa.ForeignKey("usuario.id", name="fk_horario_jornada_usuario")),
        sa.Column("creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("hora_apertura < hora_cierre", name="ck_horario_jornada_apertura_cierre"),
    )
    op.execute("INSERT INTO horario_jornada (hora_apertura, hora_cierre) VALUES ('08:00', '18:00')")

    op.add_column("transaccion", sa.Column("fecha_jornada", sa.Date, nullable=True))
    op.execute(
        f"UPDATE transaccion SET fecha_jornada = CASE WHEN {_LOCAL}::time < '18:00' THEN {_LOCAL}::date "
        f"ELSE ({_LOCAL}::date + 1) END"
    )
    op.alter_column("transaccion", "fecha_jornada", nullable=False)
    op.create_index("ix_transaccion_fecha_jornada", "transaccion", ["fecha_jornada"])

    op.add_column("reporte_diario", sa.Column("tipo", sa.String(7), nullable=True))
    op.add_column("reporte_diario", sa.Column("numero", sa.Integer, nullable=True))
    op.add_column("reporte_diario", sa.Column("cupo", sa.SmallInteger, nullable=True))
    # Los existentes: PARCIALES, numerados por jornada; los vigentes ocupan los cupos 1 a 4 (un anulado
    # conserva el cupo 1, que no cuenta porque la restricción solo mira los vigentes).
    op.execute(
        """
        UPDATE reporte_diario r SET tipo = 'PARCIAL', numero = n.numero, cupo = n.cupo
        FROM (
            SELECT id,
                   row_number() OVER (PARTITION BY fecha_jornada ORDER BY creado_en, id) AS numero,
                   CASE WHEN estado = 'ANULADO' THEN 1
                        ELSE row_number() OVER (PARTITION BY fecha_jornada, estado <> 'ANULADO'
                                                ORDER BY creado_en, id) END AS cupo
            FROM reporte_diario
        ) n
        WHERE n.id = r.id
        """
    )
    for columna in ("tipo", "numero", "cupo"):
        op.alter_column("reporte_diario", columna, nullable=False)
    op.create_check_constraint("ck_reporte_diario_tipo", "reporte_diario", "tipo IN ('PARCIAL', 'CIERRE')")
    op.create_check_constraint(
        "ck_reporte_diario_cupo",
        "reporte_diario",
        "(tipo = 'PARCIAL' AND cupo BETWEEN 1 AND 4) OR (tipo = 'CIERRE' AND cupo = 5)",
    )
    op.create_check_constraint("ck_reporte_diario_numero", "reporte_diario", "numero >= 1")
    op.create_unique_constraint("uq_reporte_diario_jornada_numero", "reporte_diario", ["fecha_jornada", "numero"])
    op.create_index(
        "uq_reporte_diario_cupo_vigente",
        "reporte_diario",
        ["fecha_jornada", "cupo"],
        unique=True,
        postgresql_where=sa.text("estado <> 'ANULADO'"),
    )
    op.create_index(
        "uq_reporte_diario_cierre_vigente",
        "reporte_diario",
        ["fecha_jornada"],
        unique=True,
        postgresql_where=sa.text("tipo = 'CIERRE' AND estado <> 'ANULADO'"),
    )

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

    op.drop_constraint("ck_evento_transaccion_tipo", "evento_transaccion", type_="check")
    op.create_check_constraint("ck_evento_transaccion_tipo", "evento_transaccion", _TIPOS_AHORA)


def downgrade() -> None:
    # Falla (sin borrar nada) si ya hay eventos EXPORTADA: la 0009 no los admite. Si se revierte, los reportes
    # pierden su tipo y su número, y se borran el historial del horario y las alertas técnicas.
    op.drop_constraint("ck_evento_transaccion_tipo", "evento_transaccion", type_="check")
    op.create_check_constraint("ck_evento_transaccion_tipo", "evento_transaccion", _TIPOS_ANTES)
    op.drop_table("alerta_tecnica")
    op.drop_index("uq_reporte_diario_cierre_vigente", table_name="reporte_diario")
    op.drop_index("uq_reporte_diario_cupo_vigente", table_name="reporte_diario")
    op.drop_constraint("uq_reporte_diario_jornada_numero", "reporte_diario", type_="unique")
    op.drop_constraint("ck_reporte_diario_numero", "reporte_diario", type_="check")
    op.drop_constraint("ck_reporte_diario_cupo", "reporte_diario", type_="check")
    op.drop_constraint("ck_reporte_diario_tipo", "reporte_diario", type_="check")
    for columna in ("cupo", "numero", "tipo"):
        op.drop_column("reporte_diario", columna)
    op.drop_index("ix_transaccion_fecha_jornada", table_name="transaccion")
    op.drop_column("transaccion", "fecha_jornada")
    op.drop_table("horario_jornada")
