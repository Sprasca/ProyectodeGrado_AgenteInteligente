"""Lectura de un documento de factura (XML UBL o PDF) a partir de sus bytes."""

from pathlib import PurePath

from ...domain.enums import TipoDocumento
from ...domain.factura import Factura
from .lector_pdf import factura_desde_pdf
from .parser import parsear_xml

EXTENSIONES = {".xml": TipoDocumento.XML, ".pdf": TipoDocumento.PDF}


def tipo_por_nombre(nombre: str | None) -> TipoDocumento | None:
    """XML o PDF según la extensión del archivo; None si no es ninguno de los dos."""
    return EXTENSIONES.get(PurePath(nombre or "").suffix.lower())


def leer_documento(contenido: bytes, tipo: TipoDocumento) -> Factura:
    """Factura de dominio desde el XML UBL (o AttachedDocument) o desde el texto del PDF."""
    return factura_desde_pdf(contenido) if tipo is TipoDocumento.PDF else parsear_xml(contenido)
