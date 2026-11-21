"""Administración del catálogo (cuentas y reglas) por el contador (RF09), con historial."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import AccionHistorial, Naturaleza
from ...domain.errors import (
    CuentaDuplicadaError,
    CuentaInvalidaError,
    ReglaInvalidaError,
    ReglaNoEncontradaError,
)
from ...domain.puc import CuentaPUC, ReglaIngreso
from ...domain.reglas import (
    ConflictoPalabra,
    ReglaAdministrada,
    exigir_guardable,
    validar_cuenta,
    validar_regla,
)
from .models import CuentaPUCModel, HistorialReglaModel, ReglaClasificacionModel
from .transacciones import RepositorioNoDisponibleError

_VIOLACION_UNICA = "23505"


@dataclass(frozen=True)
class EntradaHistorial:
    id: int
    regla_codigo: str
    accion: AccionHistorial
    antes: dict[str, Any] | None
    despues: dict[str, Any] | None
    usuario: str | None
    fecha: datetime | None


@dataclass(frozen=True)
class ReglaGuardada:
    """Regla tal como quedó y las advertencias que el contador confirmó al guardarla."""

    regla: ReglaAdministrada
    advertencias: tuple[ConflictoPalabra, ...] = ()


class RepositorioCatalogo(Protocol):
    def listar_cuentas(self) -> list[CuentaPUC]: ...

    def crear_cuenta(self, codigo: str, nombre: str, naturaleza: Naturaleza) -> CuentaPUC:
        """CuentaInvalidaError si los datos no sirven; CuentaDuplicadaError si el código ya existe."""
        ...

    def listar_reglas(self, activa: bool | None = None) -> list[ReglaAdministrada]: ...

    def obtener_regla(self, codigo: str) -> ReglaAdministrada | None: ...

    def crear_regla(
        self, regla: ReglaIngreso, *, confirmar: bool = False, usuario: str | None = None
    ) -> ReglaGuardada:
        """ReglaInvalidaError con errores; ConflictoReglaError con advertencias y sin confirmar."""
        ...

    def editar_regla(
        self,
        codigo: str,
        cuenta_ingreso: str,
        palabras_clave: Sequence[str],
        descripcion: str = "",
        *,
        confirmar: bool = False,
        usuario: str | None = None,
    ) -> ReglaGuardada:
        """Como crear_regla; el código no cambia. ReglaNoEncontradaError si no existe."""
        ...

    def activar_regla(self, codigo: str, *, usuario: str | None = None) -> ReglaAdministrada: ...

    def desactivar_regla(self, codigo: str, *, usuario: str | None = None) -> ReglaAdministrada: ...

    def historial(self, codigo: str) -> list[EntradaHistorial]:
        """Cambios de la regla, del más antiguo al más reciente. ReglaNoEncontradaError si no existe."""
        ...


def instantanea(regla: ReglaAdministrada) -> dict[str, Any]:
    """Estado de una regla para las columnas antes/despues del historial."""
    return {
        "codigo": regla.codigo,
        "cuenta_ingreso": regla.cuenta_ingreso,
        "palabras_clave": list(regla.palabras_clave),
        "descripcion": regla.descripcion,
        "activa": regla.activa,
    }


def despues_con_advertencias(regla: ReglaAdministrada, advertencias: Sequence[ConflictoPalabra]) -> dict[str, Any]:
    """Si el contador confirmó conflictos, quedan registrados junto al estado nuevo."""
    despues = instantanea(regla)
    if advertencias:
        despues["advertencias_confirmadas"] = [asdict(a) for a in advertencias]
    return despues


def regla_desde_fila(fila: ReglaClasificacionModel) -> ReglaAdministrada:
    return ReglaAdministrada(
        codigo=fila.codigo,
        cuenta_ingreso=fila.cuenta_codigo,
        palabras_clave=tuple(fila.palabras_clave),
        descripcion=fila.descripcion or "",
        activa=fila.activa,
        creado_en=fila.creado_en,
        actualizado_en=fila.actualizado_en,
    )


def _a_cuenta(fila: CuentaPUCModel) -> CuentaPUC:
    return CuentaPUC(fila.codigo, fila.nombre, Naturaleza(fila.naturaleza))


def _a_entrada(fila: HistorialReglaModel) -> EntradaHistorial:
    return EntradaHistorial(
        id=fila.id,
        regla_codigo=fila.regla_codigo,
        accion=AccionHistorial(fila.accion),
        antes=fila.antes,
        despues=fila.despues,
        usuario=fila.usuario,
        fecha=fila.fecha,
    )


def registrar_historial(
    sesion: Session,
    codigo: str,
    accion: AccionHistorial,
    antes: dict[str, Any] | None,
    despues: dict[str, Any] | None,
    usuario: str | None,
) -> None:
    """Agrega la fila de historial en la MISMA transacción que el cambio de la regla."""
    sesion.add(
        HistorialReglaModel(
            regla_codigo=codigo,
            accion=accion.value,
            antes=antes,
            despues=despues,
            usuario=usuario,
            fecha=datetime.now(UTC),
        )
    )


class RepositorioCatalogoSQL:
    """Implementación sobre PostgreSQL. al_cambiar se llama tras confirmar cada cambio (invalida la caché)."""

    def __init__(self, fabrica_sesiones: sessionmaker[Session], al_cambiar: Callable[[], None] | None = None) -> None:
        self._fabrica = fabrica_sesiones
        self._al_cambiar = al_cambiar or (lambda: None)

    @contextmanager
    def _transaccion(self) -> Iterator[Session]:
        try:
            with self._fabrica() as sesion, sesion.begin():
                yield sesion
        except (OperationalError, InterfaceError) as exc:
            raise RepositorioNoDisponibleError(
                "No hay conexión con PostgreSQL. Verifique que la base esté levantada "
                "(docker compose up -d db) y que DATABASE_URL sea correcta."
            ) from exc

    @staticmethod
    def _reglas(sesion: Session) -> list[ReglaAdministrada]:
        return [regla_desde_fila(f) for f in sesion.scalars(select(ReglaClasificacionModel))]

    @staticmethod
    def _cuentas(sesion: Session) -> list[CuentaPUC]:
        return [_a_cuenta(f) for f in sesion.scalars(select(CuentaPUCModel))]

    @staticmethod
    def _fila_regla(sesion: Session, codigo: str) -> ReglaClasificacionModel:
        fila = sesion.scalar(
            select(ReglaClasificacionModel).where(ReglaClasificacionModel.codigo == codigo).with_for_update()
        )
        if fila is None:
            raise ReglaNoEncontradaError(f"No existe la regla {codigo}.")
        return fila

    # --- cuentas ------------------------------------------------------------------------

    def listar_cuentas(self) -> list[CuentaPUC]:
        with self._transaccion() as sesion:
            consulta = select(CuentaPUCModel).order_by(CuentaPUCModel.codigo.collate("C"))
            return [_a_cuenta(f) for f in sesion.scalars(consulta)]

    def crear_cuenta(self, codigo: str, nombre: str, naturaleza: Naturaleza) -> CuentaPUC:
        codigo, nombre = codigo.strip(), nombre.strip()
        if errores := validar_cuenta(codigo, nombre):
            raise CuentaInvalidaError(errores)
        duplicada = CuentaDuplicadaError(f"Ya existe una cuenta con el código {codigo}.")
        try:
            with self._transaccion() as sesion:
                if sesion.get(CuentaPUCModel, codigo) is not None:
                    raise duplicada
                sesion.add(CuentaPUCModel(codigo=codigo, nombre=nombre, naturaleza=naturaleza.value))
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == _VIOLACION_UNICA:
                raise duplicada from exc
            raise
        self._al_cambiar()
        return CuentaPUC(codigo, nombre, naturaleza)

    # --- reglas -------------------------------------------------------------------------

    def listar_reglas(self, activa: bool | None = None) -> list[ReglaAdministrada]:
        # COLLATE "C": orden por código carácter a carácter, igual en cualquier configuración regional.
        consulta = select(ReglaClasificacionModel).order_by(ReglaClasificacionModel.codigo.collate("C"))
        if activa is not None:
            consulta = consulta.where(ReglaClasificacionModel.activa.is_(activa))
        with self._transaccion() as sesion:
            return [regla_desde_fila(f) for f in sesion.scalars(consulta)]

    def obtener_regla(self, codigo: str) -> ReglaAdministrada | None:
        with self._transaccion() as sesion:
            fila = sesion.scalar(select(ReglaClasificacionModel).where(ReglaClasificacionModel.codigo == codigo))
            return regla_desde_fila(fila) if fila is not None else None

    def crear_regla(
        self, regla: ReglaIngreso, *, confirmar: bool = False, usuario: str | None = None
    ) -> ReglaGuardada:
        try:
            with self._transaccion() as sesion:
                validacion = validar_regla(regla, self._cuentas(sesion), self._reglas(sesion), es_nueva=True)
                exigir_guardable(validacion, confirmar)
                nueva = validacion.regla
                ahora = datetime.now(UTC)
                fila = ReglaClasificacionModel(
                    codigo=nueva.codigo,
                    cuenta_codigo=nueva.cuenta_ingreso,
                    palabras_clave=list(nueva.palabras_clave),
                    descripcion=nueva.descripcion,
                    activa=True,
                    creado_en=ahora,
                    actualizado_en=ahora,
                )
                sesion.add(fila)
                sesion.flush()
                guardada = regla_desde_fila(fila)
                registrar_historial(
                    sesion,
                    guardada.codigo,
                    AccionHistorial.CREAR,
                    None,
                    despues_con_advertencias(guardada, validacion.advertencias),
                    usuario,
                )
        except IntegrityError as exc:
            # Dos altas simultáneas con el mismo código: la restricción UNIQUE decide.
            if getattr(exc.orig, "sqlstate", None) == _VIOLACION_UNICA:
                raise ReglaInvalidaError([f"Ya existe una regla con el código {regla.codigo.strip()}."]) from exc
            raise
        self._al_cambiar()
        return ReglaGuardada(guardada, validacion.advertencias)

    def editar_regla(
        self,
        codigo: str,
        cuenta_ingreso: str,
        palabras_clave: Sequence[str],
        descripcion: str = "",
        *,
        confirmar: bool = False,
        usuario: str | None = None,
    ) -> ReglaGuardada:
        with self._transaccion() as sesion:
            fila = self._fila_regla(sesion, codigo)
            antes = regla_desde_fila(fila)
            propuesta = ReglaIngreso(codigo, cuenta_ingreso, tuple(palabras_clave), descripcion)
            validacion = validar_regla(propuesta, self._cuentas(sesion), self._reglas(sesion), es_nueva=False)
            exigir_guardable(validacion, confirmar)
            editada = validacion.regla
            fila.cuenta_codigo = editada.cuenta_ingreso
            fila.palabras_clave = list(editada.palabras_clave)
            fila.descripcion = editada.descripcion
            fila.actualizado_en = datetime.now(UTC)
            sesion.flush()
            guardada = regla_desde_fila(fila)
            registrar_historial(
                sesion,
                codigo,
                AccionHistorial.EDITAR,
                instantanea(antes),
                despues_con_advertencias(guardada, validacion.advertencias),
                usuario,
            )
        self._al_cambiar()
        return ReglaGuardada(guardada, validacion.advertencias)

    def _cambiar_estado(self, codigo: str, activa: bool, usuario: str | None) -> ReglaAdministrada:
        with self._transaccion() as sesion:
            fila = self._fila_regla(sesion, codigo)
            if fila.activa is activa:
                return regla_desde_fila(fila)  # ya está así: no hay cambio que registrar
            antes = regla_desde_fila(fila)
            fila.activa = activa
            fila.actualizado_en = datetime.now(UTC)
            sesion.flush()
            guardada = regla_desde_fila(fila)
            accion = AccionHistorial.ACTIVAR if activa else AccionHistorial.DESACTIVAR
            registrar_historial(sesion, codigo, accion, instantanea(antes), instantanea(guardada), usuario)
        self._al_cambiar()
        return guardada

    def activar_regla(self, codigo: str, *, usuario: str | None = None) -> ReglaAdministrada:
        return self._cambiar_estado(codigo, True, usuario)

    def desactivar_regla(self, codigo: str, *, usuario: str | None = None) -> ReglaAdministrada:
        return self._cambiar_estado(codigo, False, usuario)

    def historial(self, codigo: str) -> list[EntradaHistorial]:
        with self._transaccion() as sesion:
            existe = sesion.scalar(
                select(ReglaClasificacionModel.id).where(ReglaClasificacionModel.codigo == codigo)
            )
            if existe is None:
                raise ReglaNoEncontradaError(f"No existe la regla {codigo}.")
            filas = sesion.scalars(
                select(HistorialReglaModel)
                .where(HistorialReglaModel.regla_codigo == codigo)
                .order_by(HistorialReglaModel.fecha, HistorialReglaModel.id)
            )
            return [_a_entrada(f) for f in filas]
