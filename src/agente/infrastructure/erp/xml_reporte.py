"""XML del reporte de transacciones diarias para el ERP (RF07, paso 5).

El manual pide un XML "con la estructura que admite el sistema contable" pero no la define, así que
esta es una estructura PROPIA y VERSIONADA (ampliación del manual; ver README), descrita por el esquema
esquemas/reporte_diario_v1.0.xsd. Cada XML se valida contra él antes de entregarse; si no cumple, la
exportación falla y el reporte sigue CONSOLIDADO (anomalía 4). Cambiar la estructura exige una versión
nueva: VERSION_FORMATO, el espacio de nombres y un archivo XSD nuevo.

    <ReporteTransaccionesDiarias xmlns="urn:agente-inteligente:reporte-diario:1.0" version="1.0">
      <Encabezado>      id, tipo (PARCIAL o CIERRE) y número en la jornada, jornada, corte, número de
                        transacciones, totales, moneda, quién y cuándo autorizó
      <Movimientos>     un <Movimiento> por código registrado (cuenta o subcuenta): débito y crédito
      <Subtotales>      un <Subtotal> por cuenta de 4 dígitos
      <Detalle>         respaldo: por transacción, comprobante (número de factura), fecha, NIT del
                        tercero (adquirente) y sus partidas con cuenta, descripción, débito y crédito
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Protocol

from lxml import etree

from ...domain.enums import Naturaleza
from ...domain.errors import XmlInvalidoError
from ...domain.jornada import en_colombia
from ...domain.reportes import ReporteDiario, TransaccionConsolidable

VERSION_FORMATO = "1.0"
ESPACIO_DE_NOMBRES = f"urn:agente-inteligente:reporte-diario:{VERSION_FORMATO}"
MONEDA = "COP"
ESQUEMA = Path(__file__).resolve().parent / "esquemas" / f"reporte_diario_v{VERSION_FORMATO}.xsd"
CERO = Decimal("0")


class TransaccionExportable(TransaccionConsolidable, Protocol):
    fecha: date
    nit_adquirente: str


def _valor(valor: Decimal) -> str:
    return f"{valor:.2f}"


def _hora(instante: datetime) -> str:
    return en_colombia(instante).isoformat(timespec="seconds")


def _hijo(padre: ET.Element, etiqueta: str, texto: object | None = None, **atributos: str) -> ET.Element:
    elemento = ET.SubElement(padre, etiqueta, atributos)
    if texto is not None:
        elemento.text = str(texto)
    return elemento


@cache
def _esquema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(ESQUEMA)))


def validar_xml_reporte(contenido: bytes) -> None:
    """XmlInvalidoError (con los primeros errores) si el XML no cumple el esquema XSD de su versión."""
    # Sin entidades externas ni red: el XML lo genera el sistema, pero no se confía en él para validar.
    analizador = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    try:
        documento = etree.fromstring(contenido, analizador)
    except etree.XMLSyntaxError as exc:
        raise XmlInvalidoError(f"El XML del reporte no está bien formado: {exc}") from exc
    esquema = _esquema()
    if not esquema.validate(documento):
        errores = "; ".join(f"línea {e.line}: {e.message}" for e in list(esquema.error_log)[:5])
        raise XmlInvalidoError(f"El XML del reporte no cumple el esquema {ESQUEMA.name}: {errores}")


def generar_xml_reporte(
    reporte: ReporteDiario,
    transacciones: Sequence[TransaccionExportable],
    autor_id: int | None,
    fecha_autorizacion: datetime,
) -> bytes:
    """El XML del reporte, ya validado contra el esquema XSD (XmlInvalidoError si no lo cumple)."""
    contenido = construir_xml_reporte(reporte, transacciones, autor_id, fecha_autorizacion)
    validar_xml_reporte(contenido)
    return contenido


def construir_xml_reporte(
    reporte: ReporteDiario,
    transacciones: Sequence[TransaccionExportable],
    autor_id: int | None,
    fecha_autorizacion: datetime,
) -> bytes:
    """El XML del reporte, en UTF-8, sin validar. Determinista: los mismos datos producen los mismos bytes."""
    raiz = ET.Element("ReporteTransaccionesDiarias", {"xmlns": ESPACIO_DE_NOMBRES, "version": VERSION_FORMATO})

    encabezado = _hijo(raiz, "Encabezado")
    _hijo(encabezado, "IdReporte", reporte.id)
    _hijo(encabezado, "TipoReporte", reporte.tipo.value)
    _hijo(encabezado, "NumeroReporte", reporte.numero)
    _hijo(encabezado, "FechaJornada", reporte.fecha_jornada.isoformat())
    _hijo(encabezado, "Corte", _hora(reporte.corte))
    _hijo(encabezado, "NumeroTransacciones", reporte.numero_transacciones)
    _hijo(encabezado, "ValorTotal", _valor(reporte.valor_total))
    _hijo(encabezado, "TotalDebito", _valor(reporte.total_debito))
    _hijo(encabezado, "TotalCredito", _valor(reporte.total_credito))
    _hijo(encabezado, "Moneda", MONEDA)
    _hijo(encabezado, "AutorizadoPor", autor_id if autor_id is not None else "")
    _hijo(encabezado, "FechaAutorizacion", _hora(fecha_autorizacion))

    movimientos = _hijo(raiz, "Movimientos")
    for m in reporte.movimientos:
        movimiento = _hijo(movimientos, "Movimiento")
        _hijo(movimiento, "Codigo", m.codigo)
        _hijo(movimiento, "Cuenta", m.cuenta)
        _hijo(movimiento, "Debito", _valor(m.debito))
        _hijo(movimiento, "Credito", _valor(m.credito))

    subtotales = _hijo(raiz, "Subtotales")
    for s in reporte.subtotales:
        subtotal = _hijo(subtotales, "Subtotal")
        _hijo(subtotal, "Cuenta", s.cuenta)
        _hijo(subtotal, "Debito", _valor(s.debito))
        _hijo(subtotal, "Credito", _valor(s.credito))

    detalle = _hijo(raiz, "Detalle")
    for t in transacciones:
        transaccion = _hijo(detalle, "Transaccion", id=str(t.id))
        _hijo(transaccion, "Comprobante", t.numero_factura)
        _hijo(transaccion, "Fecha", t.fecha.isoformat())
        _hijo(transaccion, "NitTercero", t.nit_adquirente)
        _hijo(transaccion, "ValorTotal", _valor(t.valor_total))
        partidas = _hijo(transaccion, "Partidas")
        for p in t.partidas:
            partida = _hijo(partidas, "Partida")
            _hijo(partida, "Cuenta", p.cuenta)
            _hijo(partida, "Descripcion", p.descripcion)
            _hijo(partida, "Debito", _valor(p.valor if p.naturaleza is Naturaleza.DEBITO else CERO))
            _hijo(partida, "Credito", _valor(p.valor if p.naturaleza is Naturaleza.CREDITO else CERO))

    ET.indent(raiz)
    return ET.tostring(raiz, encoding="utf-8", xml_declaration=True) + b"\n"

