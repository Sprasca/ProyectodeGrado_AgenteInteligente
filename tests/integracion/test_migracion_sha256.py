"""Migración 0008 (documento único por SHA-256) sobre una base de datos temporal en el mismo servidor.

Si ya hay documentos repetidos, la migración debe detenerse con un mensaje que liste los SHA-256 y las
transacciones afectadas, sin borrar nada. La base temporal se crea y se elimina en la prueba; la base
de DATABASE_URL no se toca.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from alembic_pruebas import configuracion_alembic
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integracion


def _alembic() -> Config:
    return configuracion_alembic()


def _transaccion_con_documento(sesion: Session, numero: str, contenido: bytes) -> uuid.UUID:
    """Con SQL plano: los modelos ORM describen el esquema de head, no el de la revisión 0007."""
    id_ = uuid.uuid4()
    sesion.execute(
        text(
            "INSERT INTO transaccion (id, numero_factura, fecha, nit_adquirente, valor_total, estado, confianza, "
            "reglas_aplicadas, motivos_alerta, detalle_lineas) VALUES (:id, :numero, :fecha, '900123456', "
            "119000.00, 'EN_ALERTA', 0.500, '{}', '[]', '[]')"
        ),
        {"id": id_, "numero": numero, "fecha": date(2026, 11, 1)},
    )
    sesion.execute(
        text(
            "INSERT INTO documento (transaccion_id, nombre_archivo, tipo, contenido, sha256, recibido_en) "
            "VALUES (:id, :nombre, 'XML', :contenido, :sha256, :recibido)"
        ),
        {
            "id": id_,
            "nombre": f"{numero}.xml",
            "contenido": contenido,
            "sha256": hashlib.sha256(contenido).hexdigest(),
            "recibido": datetime.now(UTC),
        },
    )
    return id_


def _restricciones_unicas(motor) -> set[str]:
    return {r["name"] for r in inspect(motor).get_unique_constraints("documento")}


def test_con_documentos_repetidos_la_migracion_se_detiene_y_los_lista_sin_borrar(base_temporal):
    alembic = _alembic()
    command.upgrade(alembic, "0007")
    repetido, unico = b"<Invoice>repetido</Invoice>", b"<Invoice>unico</Invoice>"
    with Session(base_temporal) as sesion, sesion.begin():
        primera = _transaccion_con_documento(sesion, "FV-REP-1", repetido)
        segunda = _transaccion_con_documento(sesion, "FV-REP-2", repetido)
        _transaccion_con_documento(sesion, "FV-UNICA", unico)

    with pytest.raises(Exception, match="No se puede crear la restricción única uq_documento_sha256") as error:
        command.upgrade(alembic, "0008")
    mensaje = str(error.value)
    assert hashlib.sha256(repetido).hexdigest() in mensaje
    assert hashlib.sha256(unico).hexdigest() not in mensaje
    for id_, numero in ((primera, "FV-REP-1"), (segunda, "FV-REP-2")):
        assert f"transacción {id_} (factura {numero})" in mensaje
    assert "No se borró nada" in mensaje

    # No se borró nada, la restricción no existe y la base sigue en 0007.
    with Session(base_temporal) as sesion:
        assert sesion.scalar(text("SELECT count(*) FROM documento")) == 3
        assert sesion.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
    assert "uq_documento_sha256" not in _restricciones_unicas(base_temporal)

    # Resuelto el repetido (decisión humana), la migración se aplica.
    with Session(base_temporal) as sesion, sesion.begin():
        sesion.execute(text("DELETE FROM documento WHERE transaccion_id = :id"), {"id": segunda})
    command.upgrade(alembic, "head")
    assert "uq_documento_sha256" in _restricciones_unicas(base_temporal)


def test_sin_repetidos_la_migracion_crea_la_restriccion_y_se_puede_revertir(base_temporal):
    alembic = _alembic()
    command.upgrade(alembic, "0007")
    with Session(base_temporal) as sesion, sesion.begin():
        _transaccion_con_documento(sesion, "FV-1", b"<Invoice>1</Invoice>")
        _transaccion_con_documento(sesion, "FV-2", b"<Invoice>2</Invoice>")
    command.upgrade(alembic, "0008")
    assert "uq_documento_sha256" in _restricciones_unicas(base_temporal)
    command.downgrade(alembic, "0007")
    assert "uq_documento_sha256" not in _restricciones_unicas(base_temporal)
