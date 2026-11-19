from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from agente.infrastructure.dian.lector_pdf import LectorPDFError, factura_desde_pdf


def _pdf(lineas: list[str]) -> bytes:
    """PDF de una página con texto embebido (seleccionable)."""
    salida = BytesIO()
    c = canvas.Canvas(salida)
    y = 800
    for linea in lineas:
        c.drawString(72, y, linea)
        y -= 20
    c.showPage()
    c.save()
    return salida.getvalue()


def test_pdf_con_texto_extrae_numero_total_e_iva():
    contenido = _pdf(
        [
            "Factura No: FV-2001",
            "Fecha: 2025-03-15",
            "NIT emisor: 900123456-1",
            "NIT cliente: 800987654-2",
            "Concepto: Venta de mercancia",
            "Subtotal: $ 100.000,00",
            "IVA 19%: $ 19.000,00",
            "Total a pagar: $ 119.000,00",
        ]
    )
    factura = factura_desde_pdf(contenido)

    assert factura.numero == "FV-2001"
    assert factura.fecha == date(2025, 3, 15)
    assert factura.total_a_pagar == Decimal("119000.00")
    assert factura.subtotal_lineas == Decimal("100000.00")
    iva = [i for i in factura.impuestos if i.codigo == "01"]
    assert len(iva) == 1
    assert iva[0].valor == Decimal("19000.00")
    assert factura.extras["origen"] == "pdf-local"


def test_pdf_sin_texto_lanza_error():
    salida = BytesIO()
    c = canvas.Canvas(salida)
    c.rect(72, 600, 200, 100, fill=1)  # solo un dibujo, como un escaneo sin OCR
    c.showPage()
    c.save()
    with pytest.raises(LectorPDFError, match="no tiene texto"):
        factura_desde_pdf(salida.getvalue())


def test_bytes_que_no_son_pdf_lanzan_error():
    with pytest.raises(LectorPDFError):
        factura_desde_pdf(b"esto no es un pdf")
