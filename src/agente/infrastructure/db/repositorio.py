from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import Naturaleza
from ...domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso
from .models import CuentaPUCModel, ReglaClasificacionModel
from .transacciones import RepositorioNoDisponibleError


def sembrar_catalogo(sesion: Session, catalogo: CatalogoPUC) -> None:
    """Carga idempotente de cuentas y reglas (se puede ejecutar varias veces)."""
    for c in catalogo.cuentas:
        sesion.merge(CuentaPUCModel(codigo=c.codigo, nombre=c.nombre, naturaleza=c.naturaleza.value))
    sesion.flush()
    existentes = {r.codigo: r for r in sesion.scalars(select(ReglaClasificacionModel))}
    for r in catalogo.reglas:
        fila = existentes.get(r.codigo) or ReglaClasificacionModel(codigo=r.codigo)
        fila.cuenta_codigo = r.cuenta_ingreso
        fila.palabras_clave = list(r.palabras_clave)
        fila.descripcion = r.descripcion
        sesion.add(fila)


def cargar_catalogo(sesion: Session) -> CatalogoPUC:
    cuentas = [CuentaPUC(c.codigo, c.nombre, Naturaleza(c.naturaleza)) for c in sesion.scalars(select(CuentaPUCModel))]
    reglas = [
        ReglaIngreso(r.codigo, r.cuenta_codigo, tuple(r.palabras_clave), r.descripcion)
        for r in sesion.scalars(select(ReglaClasificacionModel).where(ReglaClasificacionModel.activa))
    ]
    return CatalogoPUC(cuentas, reglas)


def cargar_catalogo_bd(fabrica_sesiones: sessionmaker[Session]) -> CatalogoPUC:
    """
    Catálogo desde PostgreSQL (cuentas y reglas activas).

    Lanza RepositorioNoDisponibleError si no hay conexión o si la base no tiene cuentas
    (sin sembrar): en ambos casos la API responde 503 con un mensaje claro.
    """
    try:
        with fabrica_sesiones() as sesion:
            catalogo = cargar_catalogo(sesion)
    except (OperationalError, InterfaceError) as exc:
        raise RepositorioNoDisponibleError(
            "No hay conexión con PostgreSQL para cargar el catálogo. Verifique que la base esté levantada "
            "(docker compose up -d db) y que DATABASE_URL sea correcta, o use FUENTE_CATALOGO=csv."
        ) from exc
    if not catalogo.cuentas:
        raise RepositorioNoDisponibleError(
            "El catálogo de la base de datos está vacío. Ejecute scripts/sembrar_puc.py."
        )
    return catalogo
