"""Flujo único para registrar una factura recibida, por la API o por la carpeta de entrada (RF01).

1. clasificar_documento: lee el XML o PDF y aplica la clasificación híbrida (reglas + Groq).
2. guardar_documento: persiste la transacción con el documento original, el canal y el usuario,
   junto con sus eventos RECIBIDA y CLASIFICADA.
"""

import uuid

from ..domain.documento import DocumentoOriginal
from ..domain.enums import CanalRecepcion, TipoDocumento
from ..domain.puc import CatalogoPUC
from ..domain.resultado import ResultadoClasificacion
from ..infrastructure.db.transacciones import RepositorioTransacciones
from ..infrastructure.dian.documentos import leer_documento
from .clasificar_con_nlp import clasificar_hibrido


def clasificar_documento(
    contenido: bytes, tipo: TipoDocumento, catalogo: CatalogoPUC | None = None, usar_nlp: bool = True
) -> ResultadoClasificacion:
    """Lanza los errores del lector (ParserUBLError, LectorPDFError) si el documento no se puede leer."""
    return clasificar_hibrido(leer_documento(contenido, tipo), catalogo, usar_nlp=usar_nlp)


def guardar_documento(
    resultado: ResultadoClasificacion,
    documento: DocumentoOriginal,
    repositorio: RepositorioTransacciones,
    *,
    canal: CanalRecepcion,
    usuario_id: int | None = None,
) -> uuid.UUID:
    """usuario_id es None en la recepción automática: no hay un usuario que la haga."""
    return repositorio.guardar(resultado, usuario_id=usuario_id, documento=documento, canal=canal)
