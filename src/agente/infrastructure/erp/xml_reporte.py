"""XML del reporte de transacciones diarias para el ERP (RF07, paso 5).

El manual pide un XML "con la estructura que admite el sistema contable" pero no la define, así que
esta es una estructura PROPIA y VERSIONADA (ampliación del manual; ver README). Cambiarla exige subir
VERSION_FORMATO y el espacio de nombres.

    <ReporteTransaccionesDiarias xmlns="urn:agente-inteligente:reporte-diario:1.0" version="1.0">
      <Encabezado>      id, jornada, corte, número de transacciones, totales, moneda, quién y cuándo autorizó
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
from typing import Protocol

from ...domain.enums import Naturaleza
from ...domain.jornada import en_colombia
from ...domain.reportes import ReporteDiario, TransaccionConsolidable

VERSION_FORMATO = "1.0"
ESPACIO_DE_NOMBRES = f"urn:agente-inteligente:reporte-diario:{VERSION_FORMATO}"
MONEDA = "COP"
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


def generar_xml_reporte(
    reporte: ReporteDiario,
    transacciones: Sequence[TransaccionExportable],
    autor_id: int | None,
    fecha_autorizacion: datetime,
) -> bytes:
    """El XML del reporte, en UTF-8. Determinista: los mismos datos producen los mismos bytes."""
    raiz = ET.Element("ReporteTransaccionesDiarias", {"xmlns": ESPACIO_DE_NOMBRES, "version": VERSION_FORMATO})

    encabezado = _hijo(raiz, "Encabezado")
    _hijo(encabezado, "IdReporte", reporte.id)
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

