"""Validación mínima de un documento antes de procesarlo (RF01 paso 4 y caso 2.a)."""

from pathlib import Path

import pytest
from fabricas import xml_factura
from test_pdf_en_alerta import pdf_factura

from agente.domain.enums import TipoDocumento
from agente.domain.errors import DocumentoInvalidoError
from agente.infrastructure.dian.validacion import validar_documento

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MAXIMO = 10 * 1024 * 1024
XML, PDF = TipoDocumento.XML, TipoDocumento.PDF


def _invalido(contenido, tipo, fragmento, maximo=MAXIMO):
    with pytest.raises(DocumentoInvalidoError) as error:
        validar_documento(contenido, tipo, maximo)
    assert fragmento in str(error.value)


# --- Válidos -------------------------------------------------------------------------------------


def test_invoice_valido():
    validar_documento(xml_factura("FV-1"), XML, MAXIMO)


def test_attached_document_con_invoice_embebido_valido():
    validar_documento((FIXTURES / "attached_document_comercio.xml").read_bytes(), XML, MAXIMO)


def test_el_cufe_no_se_exige_por_ahora():
    contenido = xml_factura("FV-1")
    assert b"cbc:UUID" not in contenido
    validar_documento(contenido, XML, MAXIMO)


def test_pdf_valido():
    validar_documento(pdf_factura(), PDF, MAXIMO)


# --- Inválidos: no se reintentan ---------------------------------------------------------------------


def test_vacio():
    _invalido(b"", XML, "vacío")


def test_demasiado_grande():
    _invalido(xml_factura("FV-1"), XML, "tamaño máximo de 100 bytes", maximo=100)
    _invalido(b"x" * (2 * 1024 * 1024 + 1), XML, "tamaño máximo de 2 MB", maximo=2 * 1024 * 1024)


def test_xml_incompleto_por_una_copia_cortada():
    contenido = xml_factura("FV-1")
    _invalido(contenido[: len(contenido) // 2], XML, "no está bien formado o está incompleto")


@pytest.mark.parametrize("contenido", [b"esto no es xml", b"<Invoice><cbc:ID>", b"\xff\xfe<\x00"])
def test_xml_mal_formado(contenido):
    _invalido(contenido, XML, "no está bien formado")


def test_raiz_que_no_es_factura():
    _invalido(b"<CreditNote><ID>1</ID></CreditNote>", XML, "No es una factura electrónica")


def test_attached_document_sin_invoice():
    contenido = (
        b'<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2"'
        b' xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        b"<cbc:ID>X</cbc:ID></AttachedDocument>"
    )
    _invalido(contenido, XML, "No es una factura electrónica")


@pytest.mark.parametrize(
    ("quitar", "fragmento"),
    [
        (b"<cbc:ID>FV-1</cbc:ID>", "cbc:ID"),
        (b"<cbc:IssueDate>2025-03-15</cbc:IssueDate>", "cbc:IssueDate"),
    ],
)
def test_falta_un_elemento_obligatorio(quitar, fragmento):
    contenido = xml_factura("FV-1")
    assert quitar in contenido
    _invalido(contenido.replace(quitar, b""), XML, fragmento)


def test_falta_la_linea_de_detalle():
    contenido = xml_factura("FV-1").decode()
    inicio = contenido.index("<cac:InvoiceLine>")
    fin = contenido.index("</cac:InvoiceLine>") + len("</cac:InvoiceLine>")
    _invalido((contenido[:inicio] + contenido[fin:]).encode(), XML, "cac:InvoiceLine")


def test_numero_vacio_cuenta_como_faltante():
    _invalido(xml_factura("FV-1").replace(b"<cbc:ID>FV-1</cbc:ID>", b"<cbc:ID>  </cbc:ID>"), XML, "cbc:ID")


def test_pdf_que_no_es_pdf():
    _invalido(b"hola, no soy un PDF", PDF, "no empieza con %PDF-")


def test_pdf_cortado_sin_fin_de_archivo():
    contenido = pdf_factura()
    _invalido(contenido[: len(contenido) // 2], PDF, "incompleto")


def test_pdf_con_cabecera_y_fin_pero_ilegible():
    _invalido(b"%PDF-1.4\nbasura sin estructura\n%%EOF", PDF, "no se puede leer")
