from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain.enums import Naturaleza
from ...domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso
from .models import CuentaPUCModel, ReglaClasificacionModel


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
