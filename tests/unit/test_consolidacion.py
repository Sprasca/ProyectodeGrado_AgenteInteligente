"""Consolidación (RF06): agrupación y partida doble en el dominio, y logs del caso de uso."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fabricas import resultado_alerta_con_asiento, resultado_aprobado
from repositorio_memoria import RepositorioMemoria
from repositorio_reportes_memoria import RepositorioReportesMemoria

from agente.application.reportes import consolidar_reporte
from agente.domain.asiento import Partida
from agente.domain.enums import Naturaleza
from agente.domain.errors import ConsolidacionBloqueadaError, ConsolidacionInconsistenteError, SinTransaccionesError
from agente.domain.reportes import MovimientoConsolidado, SubtotalCuenta, consolidar_movimientos

D, C = Naturaleza.DEBITO, Naturaleza.CREDITO


@dataclass(frozen=True)
class T:
    numero_factura: str
    partidas: tuple[Partida, ...]
    valor_total: Decimal = Decimal("0")
    id: uuid.UUID = uuid.UUID(int=0)


def _p(cuenta, naturaleza, valor):
    return Partida(cuenta, naturaleza, Decimal(valor))


def test_agrupa_por_codigo_registrado_con_subtotales_por_cuenta_de_4_digitos():
    transacciones = [
        T("FV-1", (_p("110505", D, "119000"), _p("4135", C, "100000"), _p("240805", C, "19000")), Decimal("119000")),
        T("FV-2", (_p("110510", D, "59500"), _p("413505", C, "50000"), _p("240805", C, "9500")), Decimal("59500")),
    ]
    c = consolidar_movimientos(transacciones)
    assert c.movimientos == (
        MovimientoConsolidado("110505", "1105", Decimal("119000"), Decimal("0")),
        MovimientoConsolidado("110510", "1105", Decimal("59500"), Decimal("0")),
        MovimientoConsolidado("240805", "2408", Decimal("0"), Decimal("28500")),
        MovimientoConsolidado("4135", "4135", Decimal("0"), Decimal("100000")),
        MovimientoConsolidado("413505", "4135", Decimal("0"), Decimal("50000")),
    )
    assert c.subtotales == (
        SubtotalCuenta("1105", Decimal("178500"), Decimal("0")),
        SubtotalCuenta("2408", Decimal("0"), Decimal("28500")),
        SubtotalCuenta("4135", Decimal("0"), Decimal("150000")),
    )
    assert (c.total_debito, c.total_credito, c.valor_total, c.numero_transacciones) == (
        Decimal("178500"),
        Decimal("178500"),
        Decimal("178500"),
        2,
    )


def test_debitos_distintos_de_creditos_es_un_error_de_integridad():
    with pytest.raises(ConsolidacionInconsistenteError, match="débitos"):
        consolidar_movimientos([T("FV-1", (_p("1105", D, "100"), _p("4135", C, "90")))])


def test_una_transaccion_aprobada_sin_partidas_es_un_error_de_integridad():
    with pytest.raises(ConsolidacionInconsistenteError, match="FV-VACIA"):
        consolidar_movimientos([T("FV-VACIA", ())])


# --- Caso de uso: jornada en hora de Colombia y logs ------------------------------------------------------


@pytest.fixture
def repos(catalogo):
    transacciones = RepositorioMemoria(catalogo)
    return transacciones, RepositorioReportesMemoria(transacciones)


def test_la_jornada_es_la_fecha_del_corte_en_colombia(repos, catalogo, caplog):
    transacciones, reportes = repos
    transacciones.guardar(resultado_aprobado(catalogo, "FV-1"))
    corte = datetime(2099, 1, 2, 3, 0, tzinfo=UTC)  # 1 de enero a las 22:00 en Colombia
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        reporte = consolidar_reporte(reportes, usuario_id=1, corte=corte)
    assert reporte.fecha_jornada == date(2099, 1, 1)
    assert "consolidado (usuario 1)" in caplog.text


def test_alertas_pendientes_dejan_un_warning_con_el_numero(repos, catalogo, caplog):
    transacciones, reportes = repos
    transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-ALERTA"))
    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(ConsolidacionBloqueadaError):
        consolidar_reporte(reportes, usuario_id=None)
    (registro,) = caplog.records
    assert registro.levelno == logging.WARNING
    assert "hay 1 transacción" in registro.getMessage() and "cierre programado" in registro.getMessage()


def test_sin_transacciones_solo_queda_en_el_log(repos, caplog):
    _, reportes = repos
    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(SinTransaccionesError):
        consolidar_reporte(reportes, usuario_id=None)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert reportes.listar() == []


def test_un_descuadre_es_una_alerta_tecnica_de_nivel_error(repos, catalogo, caplog, monkeypatch):
    transacciones, reportes = repos
    transacciones.guardar(resultado_aprobado(catalogo, "FV-1"))
    import repositorio_reportes_memoria

    def descuadrado(_):
        raise ConsolidacionInconsistenteError("Los débitos (1) no igualan a los créditos (2) del consolidado.")

    monkeypatch.setattr(repositorio_reportes_memoria, "consolidar_movimientos", descuadrado)
    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(ConsolidacionInconsistenteError):
        consolidar_reporte(reportes, usuario_id=1)
    (registro,) = caplog.records
    assert registro.levelno == logging.ERROR and "ALERTA TÉCNICA" in registro.getMessage()
    assert reportes.listar() == []
