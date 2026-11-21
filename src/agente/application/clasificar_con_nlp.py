"""Sprint 2: motor de reglas primero; Groq solo para las líneas que ninguna regla reconoce."""

from decimal import Decimal
from functools import lru_cache

from ..config import get_settings
from ..domain.clasificador import clasificar, emparejar_linea
from ..domain.enums import EstadoLinea
from ..domain.factura import Factura
from ..domain.puc import CatalogoPUC, ReglaIngreso, normalizar
from ..domain.resultado import ResultadoClasificacion
from ..infrastructure.db.repositorio import cargar_catalogo_bd
from ..infrastructure.db.session import fabrica_compartida
from ..infrastructure.dian.lector_carpeta import leer_carpeta
from ..infrastructure.llm.groq_cliente import ErrorGroq, sugerir_cuenta


@lru_cache
def catalogo_por_defecto() -> CatalogoPUC:
    """
    Catálogo con el que se clasifica, según FUENTE_CATALOGO:

    - "bd" (por defecto): cuentas y reglas ACTIVAS desde PostgreSQL, que administra el
      contador. Sin conexión lanza RepositorioNoDisponibleError (la API responde 503).
    - "csv": data/puc/*.csv (pruebas unitarias y uso sin base de datos).

    Queda en caché; invalidar_catalogo() la limpia cada vez que cambia una cuenta o regla.
    Un error no se guarda en caché: la siguiente llamada vuelve a intentar.
    """
    settings = get_settings()
    if settings.fuente_catalogo == "csv":
        return CatalogoPUC.desde_csv(settings.directorio_puc)
    return cargar_catalogo_bd(fabrica_compartida())


def invalidar_catalogo() -> None:
    """La siguiente clasificación vuelve a cargar el catálogo desde su fuente."""
    catalogo_por_defecto.cache_clear()


def recargar_catalogo() -> CatalogoPUC:
    """Invalida la caché y vuelve a cargar el catálogo (tras una actualización del contador)."""
    invalidar_catalogo()
    return catalogo_por_defecto()


def clasificar_hibrido(
    factura: Factura,
    catalogo: CatalogoPUC | None = None,
    umbral: Decimal | None = None,
    usar_nlp: bool = True,
) -> ResultadoClasificacion:
    catalogo = catalogo or catalogo_por_defecto()
    umbral = umbral if umbral is not None else get_settings().umbral_confianza

    nuevas: list[ReglaIngreso] = []
    if usar_nlp and factura.es_venta:
        candidatas = {r.cuenta_ingreso: (r.descripcion or r.codigo) for r in catalogo.reglas}
        for linea in factura.lineas:
            # Solo las líneas SIN_REGLA van al LLM; las AMBIGUA quedan para el contador.
            if emparejar_linea(linea, catalogo.reglas).estado is not EstadoLinea.SIN_REGLA:
                continue
            try:
                sugerencia = sugerir_cuenta(linea.descripcion, candidatas)
            except ErrorGroq:
                continue  # sin Groq la línea queda sin regla y la factura pasa a alerta
            if sugerencia and Decimal(str(sugerencia.confianza)) >= umbral:
                nuevas.append(
                    ReglaIngreso(
                        codigo=f"R-NLP-{sugerencia.cuenta}",
                        cuenta_ingreso=sugerencia.cuenta,
                        palabras_clave=(normalizar(linea.descripcion),),
                        descripcion=f"Sugerida por Groq: {sugerencia.justificacion}"[:200],
                    )
                )

    if nuevas:
        catalogo = CatalogoPUC(list(catalogo.cuentas), list(catalogo.reglas) + nuevas)
    return clasificar(factura, catalogo, umbral)


def procesar_carpeta(
    carpeta: str | None = None,
    recursivo: bool = False,
    usar_nlp: bool = True,
    catalogo: CatalogoPUC | None = None,
) -> list[dict]:
    salida: list[dict] = []
    for item in leer_carpeta(carpeta, recursivo):
        if item.factura is None:
            salida.append({"archivo": item.ruta.name, "error": item.error})
        else:
            resultado = clasificar_hibrido(item.factura, catalogo, usar_nlp=usar_nlp)
            salida.append({"archivo": item.ruta.name, "resultado": resultado})
    return salida
