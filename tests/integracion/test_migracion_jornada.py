"""Migración 0010 (horario, jornada de cada transacción, reportes parciales y de cierre) sobre una base temporal."""

import uuid
from datetime import date, time

import pytest
from alembic import command
from alembic_pruebas import configuracion_alembic, insertar_reporte
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integracion

JORNADA = date(2026, 11, 24)


def _transaccion(sesion: Session, creado_en: str) -> uuid.UUID:
    id_ = uuid.uuid4()
    sesion.execute(
        text(
            "INSERT INTO transaccion (id, numero_factura, fecha, nit_adquirente, valor_total, estado, confianza, "
            "reglas_aplicadas, motivos_alerta, detalle_lineas, creado_en) VALUES (:id, :numero, :fecha, "
            "'800987654', 119000.00, 'APROBADA_AUTOMATICA', 0.900, '{}', '[]', '[]', :creado_en)"
        ),
        {"id": id_, "numero": f"FV-{id_.hex[:8]}", "fecha": JORNADA, "creado_en": creado_en},
    )
    return id_


def _reporte_0009(sesion: Session, estado: str, creado_en: str) -> uuid.UUID:
    id_ = uuid.uuid4()
    sesion.execute(
        text(
            "INSERT INTO reporte_diario (id, fecha_jornada, corte, estado, numero_transacciones, total_debito, "
            "total_credito, valor_total, movimientos, subtotales, creado_en) VALUES (:id, :jornada, now(), :estado, "
            "0, 0, 0, 0, '[]', '[]', :creado_en)"
        ),
        {"id": id_, "jornada": JORNADA, "estado": estado, "creado_en": creado_en},
    )
    return id_


def test_la_migracion_0010_calcula_la_jornada_numera_los_reportes_y_se_revierte(base_temporal):
    alembic = configuracion_alembic()
    command.upgrade(alembic, "0009")
    with Session(base_temporal) as sesion, sesion.begin():
        # 17:59 y 18:00 del 24 en Colombia (UTC-5).
        antes = _transaccion(sesion, "2026-11-24 22:59:59+00")
        a_la_hora = _transaccion(sesion, "2026-11-24 23:00:00+00")
        primero = _reporte_0009(sesion, "CONSOLIDADO", "2026-11-24 10:00+00")
        anulado = _reporte_0009(sesion, "ANULADO", "2026-11-24 11:00+00")
        tercero = _reporte_0009(sesion, "CONSOLIDADO", "2026-11-24 12:00+00")

    command.upgrade(alembic, "0010")
    inspector = inspect(base_temporal)
    assert {"horario_jornada", "alerta_tecnica"} <= set(inspector.get_table_names())
    with Session(base_temporal) as sesion, sesion.begin():
        jornada = dict(sesion.execute(text("SELECT id, fecha_jornada FROM transaccion")).all())
        assert jornada == {antes: JORNADA, a_la_hora: date(2026, 11, 25)}
        horario = sesion.execute(text("SELECT hora_apertura, hora_cierre, usuario_id FROM horario_jornada")).all()
        assert horario == [(time(8, 0), time(18, 0), None)]
        reportes = {
            id_: (tipo, numero, cupo)
            for id_, tipo, numero, cupo in sesion.execute(text("SELECT id, tipo, numero, cupo FROM reporte_diario"))
        }
        assert reportes == {
            primero: ("PARCIAL", 1, 1),
            anulado: ("PARCIAL", 2, 1),
            tercero: ("PARCIAL", 3, 2),
        }
        sesion.execute(
            text("INSERT INTO evento_transaccion (transaccion_id, tipo) VALUES (:id, 'EXPORTADA')"), {"id": antes}
        )
        sesion.execute(text("DELETE FROM evento_transaccion"))

    command.downgrade(alembic, "0009")
    inspector = inspect(base_temporal)
    assert not {"horario_jornada", "alerta_tecnica"} & set(inspector.get_table_names())
    assert "fecha_jornada" not in {c["name"] for c in inspector.get_columns("transaccion")}
    with Session(base_temporal) as sesion:
        assert sesion.scalar(text("SELECT count(*) FROM reporte_diario")) == 3
    command.upgrade(alembic, "head")


def test_la_migracion_0010_se_detiene_si_una_jornada_tiene_mas_de_4_reportes_vigentes(base_temporal):
    alembic = configuracion_alembic()
    command.upgrade(alembic, "0009")
    with Session(base_temporal) as sesion, sesion.begin():
        for hora in range(10, 15):
            _reporte_0009(sesion, "CONSOLIDADO", f"2026-11-24 {hora}:00+00")
    with pytest.raises(RuntimeError, match=r"2026-11-24 \(5 reportes\)"):
        command.upgrade(alembic, "0010")
    with Session(base_temporal) as sesion:
        assert sesion.scalar(text("SELECT version_num FROM alembic_version")) == "0009"
        assert sesion.scalar(text("SELECT count(*) FROM reporte_diario")) == 5


# --- Las restricciones de la base, sin pasar por el código -------------------------------------------------


@pytest.fixture
def base(base_temporal):
    command.upgrade(configuracion_alembic(), "head")
    return base_temporal


def _falla(base, restriccion, *reportes):
    with pytest.raises(IntegrityError, match=restriccion):
        with Session(base) as sesion, sesion.begin():
            for r in reportes:
                insertar_reporte(sesion, *r)


def test_la_base_admite_4_parciales_y_un_cierre_vigentes(base):
    with Session(base) as sesion, sesion.begin():
        for n in range(1, 5):
            insertar_reporte(sesion, "PARCIAL", n, n)
        insertar_reporte(sesion, "CIERRE", 5, 5)
    with Session(base) as sesion:
        assert sesion.scalar(text("SELECT count(*) FROM reporte_diario")) == 5


def test_la_base_rechaza_un_quinto_parcial(base):
    # Un quinto parcial necesitaría el cupo 5 (del cierre) o repetir uno de los cupos 1 a 4.
    _falla(base, "ck_reporte_diario_cupo", ("PARCIAL", 1, 5))
    _falla(base, "uq_reporte_diario_cupo_vigente", ("PARCIAL", 1, 1), ("PARCIAL", 2, 1))


def test_la_base_rechaza_un_segundo_cierre_vigente(base):
    _falla(base, "uq_reporte_diario_c", ("CIERRE", 1, 5), ("CIERRE", 2, 5))
    _falla(base, "ck_reporte_diario_cupo", ("CIERRE", 1, 4))


def test_un_anulado_libera_su_cupo_pero_no_su_numero(base):
    with Session(base) as sesion, sesion.begin():
        insertar_reporte(sesion, "CIERRE", 1, 5, "ANULADO")
        insertar_reporte(sesion, "CIERRE", 2, 5)
    _falla(base, "uq_reporte_diario_jornada_numero", ("PARCIAL", 2, 1))


def test_el_horario_exige_apertura_antes_del_cierre(base):
    with pytest.raises(IntegrityError, match="ck_horario_jornada_apertura_cierre"):
        with Session(base) as sesion, sesion.begin():
            sesion.execute(text("INSERT INTO horario_jornada (hora_apertura, hora_cierre) VALUES ('18:00', '08:00')"))


def test_una_sola_alerta_de_jornada_sin_cierre_por_jornada(base_temporal):
    """En la 0010 (la 0011 la integra en las notificaciones)."""
    command.upgrade(configuracion_alembic(), "0010")
    base = base_temporal
    insertar = text(
        "INSERT INTO alerta_tecnica (tipo, fecha_jornada, mensaje) VALUES ('JORNADA_SIN_CIERRE', '2026-11-24', 'x')"
    )
    with Session(base) as sesion, sesion.begin():
        sesion.execute(insertar)
    with pytest.raises(IntegrityError, match="uq_alerta_tecnica_tipo_jornada"):
        with Session(base) as sesion, sesion.begin():
            sesion.execute(insertar)
