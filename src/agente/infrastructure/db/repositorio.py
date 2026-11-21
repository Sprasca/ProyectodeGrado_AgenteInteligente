from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import AccionHistorial, Naturaleza, OrigenRegla
from ...domain.errors import CatalogoInvalidoError, CuentaNoExisteError
from ...domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso
from .catalogo import instantanea, registrar_historial, regla_desde_fila
from .models import CuentaPUCModel, ReglaClasificacionModel
from .session import crear_sessionmaker
from .transacciones import RepositorioNoDisponibleError

# Autor de los cambios de reglas hechos por la siembra (historial_regla.usuario).
USUARIO_SIEMBRA = "scripts/sembrar_puc.py"


@dataclass
class ResumenSiembra:
    cuentas_insertadas: int = 0
    cuentas_sobrescritas: int = 0
    cuentas_omitidas: int = 0
    reglas_insertadas: int = 0
    reglas_sobrescritas: int = 0
    reglas_omitidas: int = 0
    # Con --sincronizar: reglas "csv" activas que ya no están en reglas_ingreso.csv.
    reglas_desactivadas: list[str] = field(default_factory=list)

    @property
    def insertadas(self) -> int:
        return self.cuentas_insertadas + self.reglas_insertadas

    @property
    def sobrescritas(self) -> int:
        return self.cuentas_sobrescritas + self.reglas_sobrescritas

    @property
    def omitidas(self) -> int:
        return self.cuentas_omitidas + self.reglas_omitidas


def sembrar_catalogo(
    sesion: Session, catalogo: CatalogoPUC, forzar: bool = False, sincronizar: bool = False
) -> ResumenSiembra:
    """
    Carga las cuentas y reglas de los CSV en la base. Se puede ejecutar varias veces.

    Sin forzar solo INSERTA lo que no existe: nunca toca lo que el contador editó en la base.
    Con forzar sobrescribe las existentes con lo de los CSV (nombre y naturaleza de la cuenta;
    cuenta, palabras clave y descripción de la regla). No cambia si una regla está activa.
    Las filas iguales a los CSV cuentan como omitidas. Cada regla insertada o sobrescrita
    deja su fila en historial_regla (usuario "scripts/sembrar_puc.py").

    Con sincronizar desactiva las reglas ACTIVAS de origen "csv" cuyo código ya no está en
    reglas_ingreso.csv (con DESACTIVAR en el historial). Nunca toca reglas de origen "contador".
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
            ahora = datetime.now(UTC)
            fila = ReglaClasificacionModel(
                codigo=r.codigo,
                cuenta_codigo=r.cuenta_ingreso,
                palabras_clave=list(r.palabras_clave),
                descripcion=r.descripcion,
                activa=True,
                creado_en=ahora,
                actualizado_en=ahora,
                origen=OrigenRegla.CSV.value,
            )
            sesion.add(fila)
            sesion.flush()  # la fila de historial referencia la regla (llave foránea)
            registrar_historial(
                sesion, r.codigo, AccionHistorial.CREAR, None, instantanea(regla_desde_fila(fila)), USUARIO_SIEMBRA
            )
            resumen.reglas_insertadas += 1
        elif forzar and (fila.cuenta_codigo, list(fila.palabras_clave), fila.descripcion) != valores:
            antes = instantanea(regla_desde_fila(fila))
            fila.cuenta_codigo, fila.palabras_clave, fila.descripcion = valores
            fila.actualizado_en = datetime.now(UTC)
            registrar_historial(
                sesion, r.codigo, AccionHistorial.EDITAR, antes, instantanea(regla_desde_fila(fila)), USUARIO_SIEMBRA
            )
            resumen.reglas_sobrescritas += 1
        else:
            resumen.reglas_omitidas += 1
    sesion.flush()

    if sincronizar:
        resumen.reglas_desactivadas = _desactivar_reglas_obsoletas(sesion, {r.codigo for r in catalogo.reglas})
    return resumen


def _desactivar_reglas_obsoletas(sesion: Session, codigos_csv: set[str]) -> list[str]:
    """Reglas "csv" activas que ya no están en los CSV: se desactivan (no se borran)."""
    obsoletas = sesion.scalars(
        select(ReglaClasificacionModel)
        .where(
            ReglaClasificacionModel.activa,
            ReglaClasificacionModel.origen == OrigenRegla.CSV.value,
            ReglaClasificacionModel.codigo.not_in(codigos_csv),
        )
        .order_by(ReglaClasificacionModel.codigo.collate("C"))
        .with_for_update()
    ).all()
    for fila in obsoletas:
        antes = instantanea(regla_desde_fila(fila))
        fila.activa = False
        fila.actualizado_en = datetime.now(UTC)
        registrar_historial(
            sesion,
            fila.codigo,
            AccionHistorial.DESACTIVAR,
            antes,
            instantanea(regla_desde_fila(fila)),
            USUARIO_SIEMBRA,
        )
    sesion.flush()
    return [fila.codigo for fila in obsoletas]


def sembrar_desde_csv(
    directorio: Path,
    forzar: bool = False,
    fabrica_sesiones: sessionmaker[Session] | None = None,
    sincronizar: bool = False,
) -> ResumenSiembra:
    """Lee data/puc (validando el catálogo) y lo siembra en una sola transacción."""
    catalogo = CatalogoPUC.desde_csv(directorio)
    fabrica = fabrica_sesiones or crear_sessionmaker()
    with fabrica() as sesion, sesion.begin():
        return sembrar_catalogo(sesion, catalogo, forzar=forzar, sincronizar=sincronizar)


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
            try:
                catalogo = cargar_catalogo(sesion)
            except (CatalogoInvalidoError, CuentaNoExisteError) as exc:
                raise RepositorioNoDisponibleError(_mensaje_reglas_invalidas(sesion, exc)) from exc
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


def _mensaje_reglas_invalidas(sesion: Session, exc: Exception) -> str:
    """Nombra las reglas activas que impiden cargar el catálogo y cómo resolverlo."""
    cuentas = {c.codigo: c for c in sesion.scalars(select(CuentaPUCModel))}
    invalidas: list[str] = []
    for regla in sesion.scalars(
        select(ReglaClasificacionModel)
        .where(ReglaClasificacionModel.activa)
        .order_by(ReglaClasificacionModel.codigo.collate("C"))
    ):
        cuenta = cuentas.get(regla.cuenta_codigo)
        if cuenta is None:
            invalidas.append(f"{regla.codigo} (la cuenta {regla.cuenta_codigo} no existe)")
        elif not cuenta.codigo.startswith("4") or cuenta.naturaleza != Naturaleza.CREDITO.value:
            invalidas.append(
                f"{regla.codigo} (cuenta {cuenta.codigo} {cuenta.nombre}, {cuenta.naturaleza}: "
                "no es de clase 4 con naturaleza CREDITO)"
            )
    if not invalidas:
        return f"El catálogo de la base de datos es inválido: {exc}"
    return (
        f"El catálogo de la base de datos tiene reglas activas inválidas: {'; '.join(invalidas)}. "
        "Ejecute scripts/sembrar_puc.py --sincronizar (desactiva las reglas de los CSV que ya no existen) "
        "o desactive la regla con POST /reglas/{codigo}/desactivar."
    )
