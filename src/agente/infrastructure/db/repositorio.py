from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import Naturaleza
from ...domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso
from .models import CuentaPUCModel, ReglaClasificacionModel
from .session import crear_sessionmaker
from .transacciones import RepositorioNoDisponibleError


@dataclass
class ResumenSiembra:
    cuentas_insertadas: int = 0
    cuentas_sobrescritas: int = 0
    cuentas_omitidas: int = 0
    reglas_insertadas: int = 0
    reglas_sobrescritas: int = 0
    reglas_omitidas: int = 0

    @property
    def insertadas(self) -> int:
        return self.cuentas_insertadas + self.reglas_insertadas

    @property
    def sobrescritas(self) -> int:
        return self.cuentas_sobrescritas + self.reglas_sobrescritas

    @property
    def omitidas(self) -> int:
        return self.cuentas_omitidas + self.reglas_omitidas


def sembrar_catalogo(sesion: Session, catalogo: CatalogoPUC, forzar: bool = False) -> ResumenSiembra:
    """
    Carga las cuentas y reglas de los CSV en la base. Se puede ejecutar varias veces.

    Sin forzar solo INSERTA lo que no existe: nunca toca lo que el contador editó en la base.
    Con forzar sobrescribe las existentes con lo de los CSV (nombre y naturaleza de la cuenta;
    cuenta, palabras clave y descripción de la regla). No cambia si una regla está activa.
    Las filas iguales a los CSV cuentan como omitidas.
    """
    resumen = ResumenSiembra()

    cuentas = {c.codigo: c for c in sesion.scalars(select(CuentaPUCModel))}
    for c in catalogo.cuentas:
        fila = cuentas.get(c.codigo)
        if fila is None:
            sesion.add(CuentaPUCModel(codigo=c.codigo, nombre=c.nombre, naturaleza=c.naturaleza.value))
            resumen.cuentas_insertadas += 1
        elif forzar and (fila.nombre, fila.naturaleza) != (c.nombre, c.naturaleza.value):
            fila.nombre, fila.naturaleza = c.nombre, c.naturaleza.value
            resumen.cuentas_sobrescritas += 1
        else:
            resumen.cuentas_omitidas += 1
    sesion.flush()

    reglas = {r.codigo: r for r in sesion.scalars(select(ReglaClasificacionModel))}
    for r in catalogo.reglas:
        fila = reglas.get(r.codigo)
        valores = (r.cuenta_ingreso, list(r.palabras_clave), r.descripcion)
        if fila is None:
            sesion.add(
                ReglaClasificacionModel(
                    codigo=r.codigo,
                    cuenta_codigo=r.cuenta_ingreso,
                    palabras_clave=list(r.palabras_clave),
                    descripcion=r.descripcion,
                    activa=True,
                )
            )
            resumen.reglas_insertadas += 1
        elif forzar and (fila.cuenta_codigo, list(fila.palabras_clave), fila.descripcion) != valores:
            fila.cuenta_codigo, fila.palabras_clave, fila.descripcion = valores
            resumen.reglas_sobrescritas += 1
        else:
            resumen.reglas_omitidas += 1
    sesion.flush()
    return resumen


def sembrar_desde_csv(
    directorio: Path, forzar: bool = False, fabrica_sesiones: sessionmaker[Session] | None = None
) -> ResumenSiembra:
    """Lee data/puc (validando el catálogo) y lo siembra en una sola transacción."""
    catalogo = CatalogoPUC.desde_csv(directorio)
    fabrica = fabrica_sesiones or crear_sessionmaker()
    with fabrica() as sesion, sesion.begin():
        return sembrar_catalogo(sesion, catalogo, forzar=forzar)


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
