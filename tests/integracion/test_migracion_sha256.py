"""Migración 0008 (documento único por SHA-256) sobre una base de datos temporal en el mismo servidor.

Si ya hay documentos repetidos, la migración debe detenerse con un mensaje que liste los SHA-256 y las
transacciones afectadas, sin borrar nada. La base temporal se crea y se elimina en la prueba; la base
de DATABASE_URL no se toca.
"""

import hashlib
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from agente.config import get_settings
from agente.infrastructure.db.models import DocumentoModel, TransaccionModel

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture
def base_temporal(fabrica_bd, monkeypatch):
    """Una base vacía en el mismo servidor; DATABASE_URL apunta a ella durante la prueba."""
    url = make_url(get_settings().database_url)
    nombre = f"agente_prueba_migracion_{uuid.uuid4().hex[:10]}"
    administracion = create_engine(url, isolation_level="AUTOCOMMIT")
    with administracion.connect() as conexion:
        conexion.execute(text(f'CREATE DATABASE "{nombre}"'))
    temporal = url.set(database=nombre)
    monkeypatch.setenv("DATABASE_URL", temporal.render_as_string(hide_password=False))
    get_settings.cache_clear()
    motor = create_engine(temporal)
    try:
        yield motor
    finally:
        motor.dispose()
        get_settings.cache_clear()
        with administracion.connect() as conexion:
            conexion.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        administracion.dispose()


def _alembic() -> Config:
    configuracion = Config(str(RAIZ / "alembic.ini"))
    configuracion.set_main_option("script_location", str(RAIZ / "migrations"))
    return configuracion


def _transaccion_con_documento(sesion: Session, numero: str, contenido: bytes) -> uuid.UUID:
    t = TransaccionModel(
        id=uuid.uuid4(),
        numero_factura=numero,
        fecha=date(2026, 11, 1),
        nit_adquirente="900123456",
        valor_total=Decimal("119000.00"),
        estado="EN_ALERTA",
        confianza=Decimal("0.500"),
        reglas_aplicadas=[],
        motivos_alerta=[],
        detalle_lineas=[],
    )
    sesion.add(t)
    sesion.flush()
    sesion.add(
        DocumentoModel(
            transaccion_id=t.id,
            nombre_archivo=f"{numero}.xml",
            tipo="XML",
            contenido=contenido,
            sha256=hashlib.sha256(contenido).hexdigest(),
            recibido_en=datetime.now(UTC),
        )
    )
    return t.id


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
        assert sesion.scalar(select(func.count()).select_from(DocumentoModel)) == 3
        assert sesion.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
    assert "uq_documento_sha256" not in _restricciones_unicas(base_temporal)

    # Resuelto el repetido (decisión humana), la migración se aplica.
    with Session(base_temporal) as sesion, sesion.begin():
        sesion.execute(DocumentoModel.__table__.delete().where(DocumentoModel.transaccion_id == segunda))
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
