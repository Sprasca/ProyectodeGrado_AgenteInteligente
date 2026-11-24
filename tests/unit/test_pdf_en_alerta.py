"""Toda factura recibida en PDF queda EN_ALERTA: no se puede validar contra el anexo técnico de la DIAN."""

import uuid
from decimal import Decimal
from io import BytesIO

import pytest
from autenticacion_pruebas import encabezados
from fabricas import xml_factura
from reportlab.pdfgen import canvas

import agente.application.clasificar_con_nlp as modulo
from agente.domain.clasificador import clasificar
from agente.domain.enums import EstadoTransaccion, Rol
from agente.domain.factura import MOTIVO_PDF
from agente.domain.puc import CatalogoPUC, ReglaIngreso
from agente.infrastructure.dian.lector_pdf import factura_desde_pdf
from agente.infrastructure.dian.parser import parsear_xml
from agente.infrastructure.llm.groq_cliente import SugerenciaCuenta


def pdf_factura() -> bytes:
    salida = BytesIO()
    c = canvas.Canvas(salida)
    lineas = ["Factura No: FV-2001", "Fecha: 2025-03-15", "Subtotal: $ 100.000,00", "IVA 19%: $ 19.000,00"]
    for i, linea in enumerate([*lineas, "Total a pagar: $ 119.000,00"]):
        c.drawString(72, 800 - 20 * i, linea)
    c.showPage()
    c.save()
    return salida.getvalue()


@pytest.fixture
def catalogo_que_reconoce_el_pdf(catalogo):
    """Una regla que reconoce la línea genérica del PDF ("Concepto extraído de PDF"): sin la regla nueva, aprobaría."""
    return CatalogoPUC(list(catalogo.cuentas), [*catalogo.reglas, ReglaIngreso("R-CONCEPTO", "4135", ("concepto",))])


def test_pdf_queda_en_alerta_aunque_una_regla_reconozca_todas_sus_lineas(catalogo_que_reconoce_el_pdf):
    factura = factura_desde_pdf(pdf_factura())
    assert factura.viene_de_pdf
    resultado = clasificar(factura, catalogo_que_reconoce_el_pdf, Decimal("0.80"))
    assert resultado.confianza == Decimal("1.000")  # todas las líneas clasificadas...
    assert resultado.asiento is not None
    assert resultado.estado is EstadoTransaccion.EN_ALERTA  # ...y aun así no se aprueba
    assert MOTIVO_PDF in resultado.motivos_alerta


def test_pdf_queda_en_alerta_aunque_groq_clasifique_la_linea(monkeypatch, catalogo):
    monkeypatch.setattr(modulo, "sugerir_cuenta", lambda *_: SugerenciaCuenta(cuenta="4135", confianza=0.99))
    resultado = modulo.clasificar_hibrido(factura_desde_pdf(pdf_factura()), catalogo, Decimal("0.80"))
    assert resultado.lineas[0].estado.value == "CLASIFICADA"
    assert resultado.estado is EstadoTransaccion.EN_ALERTA
    assert MOTIVO_PDF in resultado.motivos_alerta


def test_xml_no_recibe_el_motivo_del_pdf(catalogo):
    factura = parsear_xml(xml_factura())
    assert not factura.viene_de_pdf
    resultado = clasificar(factura, catalogo, Decimal("0.80"))
    assert resultado.estado is EstadoTransaccion.APROBADA_AUTOMATICA
    assert MOTIVO_PDF not in resultado.motivos_alerta


def test_pdf_cargado_por_la_api_queda_en_alerta_con_el_motivo(entorno_api, monkeypatch, catalogo_que_reconoce_el_pdf):
    from agente.api.main import app, get_catalogo

    monkeypatch.setitem(app.dependency_overrides, get_catalogo, lambda: catalogo_que_reconoce_el_pdf)
    r = entorno_api.cliente.post(
        "/facturas/clasificar",
        files={"archivo": ("factura.pdf", pdf_factura(), "application/pdf")},
        params={"usar_nlp": "false"},
        headers=encabezados(Rol.AUXILIAR),
    )
    assert r.status_code == 200, r.text
    datos = r.json()
    assert datos["estado"] == "EN_ALERTA"
    assert MOTIVO_PDF in datos["motivos_alerta"]
    guardada = entorno_api.transacciones.obtener(uuid.UUID(datos["id"]))
    assert guardada.estado is EstadoTransaccion.EN_ALERTA
