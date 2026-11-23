import re
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


def test_attached_document_extrae_igual_que_invoice_directo():
    directo = parsear_xml((FIXTURES / "factura_comercio_contado.xml").read_bytes())
    adjunto = parsear_xml((FIXTURES / "attached_document_comercio.xml").read_bytes())

    assert adjunto == directo
    assert adjunto.numero == "FV-1001"  # el del Invoice embebido, no el ID del contenedor
    assert adjunto.total_a_pagar == Decimal("119000.00")


def test_xml_sin_numero_recibe_numero_provisional():
    xml = (FIXTURES / "factura_comercio_contado.xml").read_text(encoding="utf-8")
    factura = parsear_xml(xml.replace("<cbc:ID>FV-1001</cbc:ID>", ""))
    assert re.fullmatch(r"SIN-NUMERO-[0-9a-f]{8}", factura.numero)
    assert factura.tiene_numero_provisional
    assert not parsear_xml(xml).tiene_numero_provisional


def test_attached_document_sin_invoice_lanza_error():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>AD-VACIO</cbc:ID>
  <cac:Attachment>
    <cac:ExternalReference>
      <cbc:Description>Documento sin factura embebida</cbc:Description>
    </cac:ExternalReference>
  </cac:Attachment>
</AttachedDocument>"""
    with pytest.raises(ParserUBLError, match="no contiene un Invoice"):
        parsear_xml(xml)


# --- CUFE (cbc:UUID) ------------------------------------------------------------------------------

CUFE = "a" * 32 + "b" * 32 + "c" * 32  # 96 caracteres, como un SHA-384 en hexadecimal


def _con_cufe(xml: str) -> str:
    """Agrega cbc:UUID al Invoice (también al que viene embebido en un AttachedDocument)."""
    return xml.replace(
        "<cbc:ID>FV-1001</cbc:ID>", f'<cbc:ID>FV-1001</cbc:ID><cbc:UUID schemeName="CUFE-SHA384">{CUFE}</cbc:UUID>'
    )


def test_extrae_el_cufe_del_invoice():
    xml = _con_cufe((FIXTURES / "factura_comercio_contado.xml").read_text(encoding="utf-8"))
    assert parsear_xml(xml).cufe == CUFE


def test_extrae_el_cufe_del_invoice_dentro_de_attached_document():
    adjunto = _con_cufe((FIXTURES / "attached_document_comercio.xml").read_text(encoding="utf-8"))
    assert "<AttachedDocument" in adjunto and CUFE in adjunto
    factura = parsear_xml(adjunto)
    assert factura.cufe == CUFE
    assert factura.numero == "FV-1001"


def test_sin_cbc_uuid_el_cufe_es_none():
    assert parsear_xml((FIXTURES / "factura_comercio_contado.xml").read_bytes()).cufe is None
    assert parsear_xml((FIXTURES / "attached_document_comercio.xml").read_bytes()).cufe is None


def test_cbc_uuid_vacio_es_none():
    xml = (FIXTURES / "factura_comercio_contado.xml").read_text(encoding="utf-8").replace(
        "<cbc:ID>FV-1001</cbc:ID>", "<cbc:ID>FV-1001</cbc:ID><cbc:UUID>  </cbc:UUID>"
    )
    assert parsear_xml(xml).cufe is None

