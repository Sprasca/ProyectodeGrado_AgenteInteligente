from decimal import Decimal
from pathlib import Path

import pytest

from agente.domain.enums import CondicionPago
from agente.infrastructure.dian.parser import ParserUBLError, parsear_xml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parsea_factura_comercio():
    xml = (FIXTURES / "factura_comercio_contado.xml").read_text(encoding="utf-8")
    factura = parsear_xml(xml)

    assert factura.numero == "FV-1001"
    assert factura.tipo_documento == "01"
    assert factura.es_venta is True
    assert factura.condicion_pago is CondicionPago.CONTADO
    assert factura.medio_pago == "10"
    assert len(factura.lineas) == 1
    assert "mercancía" in factura.lineas[0].descripcion.lower()
    assert factura.total_a_pagar == Decimal("119000.00")
    assert any(i.codigo == "01" for i in factura.impuestos)


def test_xml_invalido_lanza_error():
    with pytest.raises(ParserUBLError):
        parsear_xml("<no-es-factura></no-es-factura>")
