"""Validación mínima de un documento recibido antes de procesarlo (RF01, paso 4 y caso 2.a).

No es una validación completa contra el XSD del anexo técnico de la DIAN (no se dispone del
esquema): comprueba que el archivo sea un XML o PDF completo y legible, y en el XML que tenga la
estructura mínima de una factura electrónica.

Pendiente con el contador: exigir el CUFE (cbc:UUID). Hoy no se exige porque las facturas de
prueba no lo traen.
"""

from io import BytesIO
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from ...domain.enums import TipoDocumento
from ...domain.errors import DocumentoInvalidoError
from .parser import NS, ParserUBLError, extraer_invoice

# Elementos mínimos del Invoice (UBL 2.1 / anexo técnico DIAN) que se exigen.
ELEMENTOS_OBLIGATORIOS = {
    "cbc:ID": "el número de la factura (cbc:ID)",
    "cbc:IssueDate": "la fecha de emisión (cbc:IssueDate)",
    "cac:InvoiceLine": "al menos una línea de detalle (cac:InvoiceLine)",
}


def validar_documento(contenido: bytes, tipo: TipoDocumento, tamano_maximo: int) -> None:
    """Lanza DocumentoInvalidoError con el motivo si el documento no se puede procesar."""
    if not contenido:
        raise DocumentoInvalidoError("El archivo está vacío.")
    if len(contenido) > tamano_maximo:
        megas, resto = divmod(tamano_maximo, 1024 * 1024)
        limite = f"{megas} MB" if megas and not resto else f"{tamano_maximo} bytes"
        raise DocumentoInvalidoError(f"El archivo supera el tamaño máximo de {limite}.")
    if tipo is TipoDocumento.PDF:
        _validar_pdf(contenido)
    else:
        _validar_xml(contenido)


def _validar_xml(contenido: bytes) -> None:
    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError as exc:
        raise DocumentoInvalidoError(f"El XML no está bien formado o está incompleto: {exc}.") from exc
    try:
        factura = extraer_invoice(raiz)  # Invoice directo o embebido en un AttachedDocument
    except ParserUBLError as exc:
        raise DocumentoInvalidoError(f"No es una factura electrónica: {exc}") from exc

    faltantes = [
        descripcion
        for ruta, descripcion in ELEMENTOS_OBLIGATORIOS.items()
        if (nodo := factura.find(ruta, NS)) is None or (ruta.startswith("cbc:") and not (nodo.text or "").strip())
    ]
    if faltantes:
        raise DocumentoInvalidoError(
            "La factura no se ajusta a la estructura mínima del anexo técnico de la DIAN: falta "
            + ", ".join(faltantes)
            + "."
        )


def _validar_pdf(contenido: bytes) -> None:
    if not contenido.startswith(b"%PDF-"):
        raise DocumentoInvalidoError("El archivo no es un PDF (no empieza con %PDF-).")
    if b"%%EOF" not in contenido[-1024:]:
        raise DocumentoInvalidoError("El PDF está incompleto (no termina con %%EOF).")
    try:
        paginas = len(PdfReader(BytesIO(contenido)).pages)
    except Exception as exc:
        raise DocumentoInvalidoError(f"El PDF no se puede leer: {exc}") from exc
    if paginas == 0:
        raise DocumentoInvalidoError("El PDF no tiene páginas.")
