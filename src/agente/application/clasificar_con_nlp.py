"""Sprint 2: motor de reglas primero; Groq solo para las líneas que ninguna regla reconoce."""

from decimal import Decimal
from functools import lru_cache

from ..config import get_settings
from ..domain.clasificador import clasificar
from ..domain.factura import Factura
from ..domain.puc import CatalogoPUC, ReglaIngreso, normalizar
from ..domain.resultado import ResultadoClasificacion
from ..infrastructure.dian.lector_carpeta import leer_carpeta
from ..infrastructure.llm.groq_cliente import ErrorGroq, sugerir_cuenta


@lru_cache
def catalogo_por_defecto() -> CatalogoPUC:
    """Catálogo desde los CSV de data/puc (no requiere base de datos para la demo)."""
    return CatalogoPUC.desde_csv(get_settings().directorio_puc)


def _tiene_regla(descripcion: str, catalogo: CatalogoPUC) -> bool:
    texto = normalizar(descripcion)
    return any(p in texto for r in catalogo.reglas for p in r.palabras_clave)


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
            if _tiene_regla(linea.descripcion, catalogo):
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


def procesar_carpeta(carpeta: str | None = None, recursivo: bool = False, usar_nlp: bool = True) -> list[dict]:
    salida: list[dict] = []
    for item in leer_carpeta(carpeta, recursivo):
        if item.factura is None:
            salida.append({"archivo": item.ruta.name, "error": item.error})
        else:
            salida.append({"archivo": item.ruta.name, "resultado": clasificar_hibrido(item.factura, usar_nlp=usar_nlp)})
    return salida