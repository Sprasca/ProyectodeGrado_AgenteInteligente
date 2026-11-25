"""XML del reporte diario para el ERP (RF07) y caso de uso de exportación."""

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fabricas import resultado_aprobado
from repositorio_memoria import RepositorioMemoria
from repositorio_reportes_memoria import RepositorioReportesMemoria

from agente.application.reportes import consolidar_reporte, exportar_reporte
from agente.domain.asiento import Partida
from agente.domain.enums import EstadoReporte
from agente.domain.errors import ExportacionFallidaError
from agente.domain.jornada import HORARIO_INICIAL
from agente.infrastructure.erp.xml_reporte import ESPACIO_DE_NOMBRES, VERSION_FORMATO, generar_xml_reporte

NS = {"r": ESPACIO_DE_NOMBRES}
AUTORIZACION = datetime(2026, 11, 24, 23, 15, 0, tzinfo=UTC)  # 18:15 en Colombia


@pytest.fixture
def consolidado(catalogo):
    transacciones = RepositorioMemoria(catalogo)
    reportes = RepositorioReportesMemoria(transacciones)
    for numero in ("FV-1", "FV-2"):
        transacciones.guardar(resultado_aprobado(catalogo, numero))
    reporte = consolidar_reporte(reportes, HORARIO_INICIAL, usuario_id=2)
    incluidas = [transacciones.obtener(i) for i in reporte.transacciones]
    return reporte, incluidas, transacciones, reportes


def _texto(nodo, ruta):
    return nodo.find(ruta, NS).text


def test_estructura_version_y_encabezado(consolidado):
    reporte, incluidas, *_ = consolidado
    raiz = ET.fromstring(generar_xml_reporte(reporte, incluidas, 1, AUTORIZACION))
    assert raiz.tag == f"{{{ESPACIO_DE_NOMBRES}}}ReporteTransaccionesDiarias"
    assert raiz.get("version") == VERSION_FORMATO
    encabezado = raiz.find("r:Encabezado", NS)
    assert _texto(encabezado, "r:IdReporte") == str(reporte.id)
    assert _texto(encabezado, "r:FechaJornada") == reporte.fecha_jornada.isoformat()
    assert _texto(encabezado, "r:NumeroTransacciones") == "2"
    assert _texto(encabezado, "r:TotalDebito") == _texto(encabezado, "r:TotalCredito") == f"{reporte.total_debito:.2f}"
    assert _texto(encabezado, "r:ValorTotal") == "238000.00"
    assert _texto(encabezado, "r:Moneda") == "COP"
    assert _texto(encabezado, "r:AutorizadoPor") == "1"
    assert _texto(encabezado, "r:FechaAutorizacion") == "2026-11-24T18:15:00-05:00"  # hora de Colombia


def test_totales_por_codigo_y_subtotales_por_cuenta(consolidado):
    reporte, incluidas, *_ = consolidado
    raiz = ET.fromstring(generar_xml_reporte(reporte, incluidas, 1, AUTORIZACION))
    movimientos = [
        (_texto(m, "r:Codigo"), _texto(m, "r:Cuenta"), Decimal(_texto(m, "r:Debito")), Decimal(_texto(m, "r:Credito")))
        for m in raiz.findall("r:Movimientos/r:Movimiento", NS)
    ]
    assert movimientos == [(m.codigo, m.cuenta, m.debito, m.credito) for m in reporte.movimientos]
    nodos = raiz.findall("r:Subtotales/r:Subtotal", NS)
    subtotales = {_texto(s, "r:Cuenta"): Decimal(_texto(s, "r:Debito")) for s in nodos}
    assert subtotales == {s.cuenta: s.debito for s in reporte.subtotales}
    assert sum(m[2] for m in movimientos) == sum(m[3] for m in movimientos)


def test_detalle_por_transaccion_con_nit_del_tercero(consolidado):
    reporte, incluidas, *_ = consolidado
    raiz = ET.fromstring(generar_xml_reporte(reporte, incluidas, 1, AUTORIZACION))
    detalle = raiz.findall("r:Detalle/r:Transaccion", NS)
    assert [_texto(t, "r:Comprobante") for t in detalle] == ["FV-1", "FV-2"]
    primera, transaccion = detalle[0], incluidas[0]
    assert primera.get("id") == str(transaccion.id)
    assert _texto(primera, "r:NitTercero") == transaccion.nit_adquirente == "800987654"
    assert _texto(primera, "r:Fecha") == transaccion.fecha.isoformat()
    partidas = primera.findall("r:Partidas/r:Partida", NS)
    assert len(partidas) == len(transaccion.partidas)
    debitos = sum(Decimal(_texto(p, "r:Debito")) for p in partidas)
    creditos = sum(Decimal(_texto(p, "r:Credito")) for p in partidas)
    assert debitos == creditos == transaccion.valor_total


def test_caracteres_especiales_se_escapan_y_es_determinista(consolidado):
    reporte, incluidas, *_ = consolidado
    p = incluidas[0].partidas[0]
    rara = replace(incluidas[0], partidas=(replace(p, descripcion='Café & "té" <ñ>'), *incluidas[0].partidas[1:]))
    contenido = generar_xml_reporte(reporte, [rara, incluidas[1]], 1, AUTORIZACION)
    raiz = ET.fromstring(contenido)  # bien formado
    assert raiz.find("r:Detalle/r:Transaccion/r:Partidas/r:Partida/r:Descripcion", NS).text == 'Café & "té" <ñ>'
    assert contenido.startswith(b"<?xml version='1.0' encoding='utf-8'?>")
    assert contenido == generar_xml_reporte(reporte, [rara, incluidas[1]], 1, AUTORIZACION)


# --- Caso de uso ------------------------------------------------------------------------------------------


def test_exportar_reporte_genera_el_xml_real(consolidado, caplog):
    reporte, _, _, reportes = consolidado
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        exportado = exportar_reporte(reportes, reporte.id, usuario_id=1)
    archivo = reportes.archivo(reporte.id)
    assert exportado.estado is EstadoReporte.EXPORTADO
    assert hashlib.sha256(archivo.contenido).hexdigest() == exportado.archivo_sha256
    assert ET.fromstring(archivo.contenido).get("version") == VERSION_FORMATO
    assert exportado.archivo_sha256 in caplog.text


def test_si_la_generacion_falla_queda_un_error_en_el_log(consolidado, caplog):
    reporte, _, _, reportes = consolidado

    def falla(*_):
        raise ValueError("estructura inválida")

    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(ExportacionFallidaError):
        exportar_reporte(reportes, reporte.id, usuario_id=1, generar=falla)
    (registro,) = caplog.records
    assert registro.levelno == logging.ERROR and "estructura inválida" in registro.getMessage()
    assert reportes.obtener(reporte.id).estado is EstadoReporte.CONSOLIDADO


def test_partida_sin_descripcion(consolidado):
    reporte, incluidas, *_ = consolidado
    sin = replace(incluidas[0], partidas=tuple(Partida(p.cuenta, p.naturaleza, p.valor) for p in incluidas[0].partidas))
    raiz = ET.fromstring(generar_xml_reporte(reporte, [sin], None, AUTORIZACION))
    assert raiz.find("r:Detalle/r:Transaccion/r:Partidas/r:Partida/r:Descripcion", NS).text in (None, "")
    assert raiz.find("r:Encabezado/r:AutorizadoPor", NS).text in (None, "")
