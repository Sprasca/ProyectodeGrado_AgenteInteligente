"""Contrato de RepositorioReportes contra PostgreSQL, sobre una base temporal (ver bd_sembrada)."""

import itertools
from datetime import UTC, datetime

import pytest
from autenticacion_pruebas import CONTRASENA
from contrato_reportes import JORNADA, ContratoReportes
from fabricas import resultado_aprobado
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from agente.domain.enums import EstadoReporte, Rol, TipoEvento, TipoReporte
from agente.infrastructure.db.models import ReporteDiarioModel, TransaccionModel
from agente.infrastructure.db.reportes import RepositorioReportesSQL
from agente.infrastructure.db.transacciones import RepositorioSQL
from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def transacciones(bd_sembrada, catalogo):
    return RepositorioSQL(bd_sembrada, catalogo)


@pytest.fixture
def reportes(bd_sembrada):
    return RepositorioReportesSQL(bd_sembrada)


@pytest.fixture
def id_usuario(bd_sembrada):
    return RepositorioUsuariosSQL(bd_sembrada).crear("contadora", "Contadora", CONTRASENA, Rol.CONTADOR).id


@pytest.fixture
def nuevo_numero():
    contador = itertools.count(1)
    return lambda: f"FV-REP-{next(contador)}"


@pytest.fixture
def mover_a_jornada(bd_sembrada):
    def mover(id_, fecha):
        with bd_sembrada() as sesion, sesion.begin():
            sesion.execute(update(TransaccionModel).where(TransaccionModel.id == id_).values(fecha_jornada=fecha))

    return mover


class TestRepositorioReportesSQL(ContratoReportes):
    pass


# --- Solo PostgreSQL ----------------------------------------------------------------------------------------


def _consolidado(transacciones, reportes, catalogo, mover_a_jornada):
    id_ = transacciones.guardar(resultado_aprobado(catalogo, "FV-ATOMICA-1"))
    mover_a_jornada(id_, JORNADA)
    reporte = reportes.consolidar(fecha_jornada=JORNADA, tipo=TipoReporte.PARCIAL, corte=datetime.now(UTC))
    return id_, reporte


def test_generar_y_marcar_exportado_ocurren_en_una_sola_transaccion(
    bd_sembrada, transacciones, reportes, catalogo, mover_a_jornada
):
    """El XML se genera, pero la base rechaza lo que sigue (un autor que no existe): no queda nada a medias."""
    id_, reporte = _consolidado(transacciones, reportes, catalogo, mover_a_jornada)
    with pytest.raises(IntegrityError):
        reportes.exportar(reporte.id, usuario_id=999_999, generar=lambda *_: b"<x/>")
    assert reportes.obtener(reporte.id) == reporte
    assert reportes.archivo(reporte.id) is None
    assert [e.tipo for e in reportes.eventos(reporte.id)] == [EstadoReporte.CONSOLIDADO]
    assert TipoEvento.EXPORTADA not in [e.tipo for e in transacciones.eventos(id_)]


def test_el_xml_exportado_queda_guardado_en_la_base(
    bd_sembrada, transacciones, reportes, catalogo, mover_a_jornada, id_usuario
):
    _, reporte = _consolidado(transacciones, reportes, catalogo, mover_a_jornada)
    exportado = reportes.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<guardado/>")
    with bd_sembrada() as sesion:
        fila = sesion.get(ReporteDiarioModel, reporte.id)
        assert (fila.estado, fila.archivo_xml, fila.archivo_sha256) == (
            "EXPORTADO",
            b"<guardado/>",
            exportado.archivo_sha256,
        )
