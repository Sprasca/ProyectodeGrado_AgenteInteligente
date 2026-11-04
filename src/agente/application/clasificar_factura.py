"""Caso de uso principal del Sprint 1: parsear XML y clasificar con reglas."""

from decimal import Decimal

from ..config import get_settings
from ..domain.clasificador import clasificar
from ..domain.puc import CatalogoPUC
from ..domain.resultado import ResultadoClasificacion
from ..infrastructure.db.repositorio import cargar_catalogo
from ..infrastructure.db.session import crear_sessionmaker
from ..infrastructure.dian.parser import parsear_xml


def clasificar_factura(
    xml: str | bytes,
    catalogo: CatalogoPUC | None = None,
    umbral_confianza: Decimal | None = None,
) -> ResultadoClasificacion:
    """
    Orquesta el flujo completo del Sprint 1:

    1. Parsea el XML UBL → Factura de dominio.
    2. Carga el catálogo (en memoria o desde BD si no se pasa).
    3. Aplica el motor de reglas.
    4. Devuelve ResultadoClasificacion listo para persistir o mostrar.

    Este caso de uso es independiente del framework web (FastAPI, Groq, etc.).
    """
    settings = get_settings()
    umbral = umbral_confianza if umbral_confianza is not None else settings.umbral_confianza

    factura = parsear_xml(xml)

    if catalogo is None:
        # Carga desde BD (después de sembrar_puc)
        with crear_sessionmaker()() as sesion:
            catalogo = cargar_catalogo(sesion)

    return clasificar(factura, catalogo, umbral)