"""Migración 0009 (reportes diarios) sobre una base temporal: sube con datos existentes y se puede revertir."""

import uuid
from datetime import date

import pytest
from alembic import command
from alembic_pruebas import configuracion_alembic, insertar_reporte
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integracion

TABLAS = {"reporte_diario", "reporte_transaccion", "evento_reporte"}


def _transaccion(sesion: Session) -> uuid.UUID:
    id_ = uuid.uuid4()
    sesion.execute(
        text(
            "INSERT INTO transaccion (id, numero_factura, fecha, nit_adquirente, valor_total, estado, confianza, "
            "reglas_aplicadas, motivos_alerta, detalle_lineas) VALUES (:id, :numero, :fecha, '800987654', "
            "119000.00, 'APROBADA_AUTOMATICA', 0.900, '{}', '[]', '[]')"
        ),
        {"id": id_, "numero": f"FV-{id_.hex[:8]}", "fecha": date(2026, 11, 24)},
    )
    return id_


def _reporte(sesion: Session, estado: str = "CONSOLIDADO") -> None:
    insertar_reporte(sesion, "PARCIAL", 1, 1, estado)


def test_la_migracion_0009_sube_con_datos_y_se_revierte(base_temporal):
    alembic = configuracion_alembic()
    command.upgrade(alembic, "0008")
    with Session(base_temporal) as sesion, sesion.begin():
        existente = _transaccion(sesion)

    command.upgrade(alembic, "0009")
    inspector = inspect(base_temporal)
    assert TABLAS <= set(inspector.get_table_names())
    assert "reporte_id" in {c["name"] for c in inspector.get_columns("transaccion")}
    with Session(base_temporal) as sesion, sesion.begin():
        # La transacción que ya existía queda sin reporte.
        assert sesion.scalar(text("SELECT reporte_id FROM transaccion WHERE id = :id"), {"id": existente}) is None
        # Los eventos nuevos de transacción se aceptan.
        sesion.execute(
            text("INSERT INTO evento_transaccion (transaccion_id, tipo) VALUES (:id, 'CONSOLIDADA')"), {"id": existente}
        )
        sesion.execute(text("DELETE FROM evento_transaccion WHERE transaccion_id = :id"), {"id": existente})

    command.downgrade(alembic, "0008")
    inspector = inspect(base_temporal)
    assert not TABLAS & set(inspector.get_table_names())
    assert "reporte_id" not in {c["name"] for c in inspector.get_columns("transaccion")}
    with Session(base_temporal) as sesion:
        assert sesion.scalar(text("SELECT count(*) FROM transaccion")) == 1
    command.upgrade(alembic, "head")


def test_un_reporte_exportado_sin_archivo_lo_rechaza_la_base(base_temporal):
    command.upgrade(configuracion_alembic(), "head")
    with Session(base_temporal) as sesion, sesion.begin():
        _reporte(sesion, "CONSOLIDADO")  # sin archivo: válido
    with pytest.raises(IntegrityError, match="ck_reporte_diario_exportado_con_archivo"):
        with Session(base_temporal) as sesion, sesion.begin():
            _reporte(sesion, "EXPORTADO")
    with pytest.raises(IntegrityError, match="ck_reporte_diario_estado"):
        with Session(base_temporal) as sesion, sesion.begin():
            _reporte(sesion, "AUTORIZADO")
