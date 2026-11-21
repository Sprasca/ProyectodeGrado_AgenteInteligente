"""Repositorio del catálogo en memoria. SOLO para pruebas; mismo comportamiento que RepositorioCatalogoSQL."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from itertools import count

from agente.domain.enums import AccionHistorial, Naturaleza, OrigenRegla
from agente.domain.errors import CuentaDuplicadaError, CuentaInvalidaError, ReglaNoEncontradaError
from agente.domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso
from agente.domain.reglas import ReglaAdministrada, exigir_guardable, validar_cuenta, validar_regla
from agente.infrastructure.db.catalogo import (
    EntradaHistorial,
    ReglaGuardada,
    despues_con_advertencias,
    instantanea,
)


class RepositorioCatalogoMemoria:
    def __init__(
        self,
        cuentas: Sequence[CuentaPUC] = (),
        reglas: Sequence[ReglaAdministrada] = (),
        al_cambiar: Callable[[], None] | None = None,
    ) -> None:
        self._cuentas = {c.codigo: c for c in cuentas}
        self._reglas = {r.codigo: r for r in reglas}
        self._historial: list[EntradaHistorial] = []
        self._ids = count(1)
        self._al_cambiar = al_cambiar or (lambda: None)

    @classmethod
    def desde_catalogo(cls, catalogo: CatalogoPUC, al_cambiar: Callable[[], None] | None = None):
        """Como una base recién sembrada con ese catálogo (todas las reglas activas, sin historial)."""
        reglas = [
            ReglaAdministrada(r.codigo, r.cuenta_ingreso, r.palabras_clave, r.descripcion, origen=OrigenRegla.CSV)
            for r in catalogo.reglas
        ]
        return cls(catalogo.cuentas, reglas, al_cambiar)

    def como_catalogo(self) -> CatalogoPUC:
        """Lo que cargaría cargar_catalogo(): todas las cuentas y solo las reglas activas."""
        activas = [r.como_regla_ingreso() for r in sorted(self._reglas.values(), key=lambda r: r.codigo) if r.activa]
        return CatalogoPUC(list(self._cuentas.values()), activas)

    def _registrar(self, codigo, accion, antes, despues, usuario) -> None:
        self._historial.append(
            EntradaHistorial(next(self._ids), codigo, accion, antes, despues, usuario, datetime.now(UTC))
        )

    def _regla(self, codigo: str) -> ReglaAdministrada:
        regla = self._reglas.get(codigo)
        if regla is None:
            raise ReglaNoEncontradaError(f"No existe la regla {codigo}.")
        return regla

    # --- cuentas ------------------------------------------------------------------------

    def listar_cuentas(self) -> list[CuentaPUC]:
        return sorted(self._cuentas.values(), key=lambda c: c.codigo)

    def crear_cuenta(self, codigo: str, nombre: str, naturaleza: Naturaleza) -> CuentaPUC:
        codigo, nombre = codigo.strip(), nombre.strip()
        if errores := validar_cuenta(codigo, nombre):
            raise CuentaInvalidaError(errores)
        if codigo in self._cuentas:
            raise CuentaDuplicadaError(f"Ya existe una cuenta con el código {codigo}.")
        cuenta = CuentaPUC(codigo, nombre, naturaleza)
        self._cuentas[codigo] = cuenta
        self._al_cambiar()
        return cuenta

    # --- reglas -------------------------------------------------------------------------

    def listar_reglas(self, activa: bool | None = None) -> list[ReglaAdministrada]:
        reglas = sorted(self._reglas.values(), key=lambda r: r.codigo)
        return [r for r in reglas if activa is None or r.activa is activa]

    def obtener_regla(self, codigo: str) -> ReglaAdministrada | None:
        return self._reglas.get(codigo)

    def crear_regla(
        self, regla: ReglaIngreso, *, confirmar: bool = False, usuario: str | None = None
    ) -> ReglaGuardada:
        validacion = validar_regla(regla, self._cuentas.values(), self._reglas.values(), es_nueva=True)
        exigir_guardable(validacion, confirmar)
        nueva = validacion.regla
        ahora = datetime.now(UTC)
        guardada = ReglaAdministrada(
            nueva.codigo,
            nueva.cuenta_ingreso,
            nueva.palabras_clave,
            nueva.descripcion,
            True,
            ahora,
            ahora,
            OrigenRegla.CONTADOR,
        )
        self._reglas[guardada.codigo] = guardada
        self._registrar(
            guardada.codigo,
            AccionHistorial.CREAR,
            None,
            despues_con_advertencias(guardada, validacion.advertencias),
            usuario,
        )
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
        antes = self._regla(codigo)
        propuesta = ReglaIngreso(codigo, cuenta_ingreso, tuple(palabras_clave), descripcion)
        validacion = validar_regla(propuesta, self._cuentas.values(), self._reglas.values(), es_nueva=False)
        exigir_guardable(validacion, confirmar)
        editada = validacion.regla
        guardada = replace(
            antes,
            cuenta_ingreso=editada.cuenta_ingreso,
            palabras_clave=editada.palabras_clave,
            descripcion=editada.descripcion,
            actualizado_en=datetime.now(UTC),
        )
        self._reglas[codigo] = guardada
        self._registrar(
            codigo,
            AccionHistorial.EDITAR,
            instantanea(antes),
            despues_con_advertencias(guardada, validacion.advertencias),
            usuario,
        )
        self._al_cambiar()
        return ReglaGuardada(guardada, validacion.advertencias)

    def _cambiar_estado(self, codigo: str, activa: bool, usuario: str | None) -> ReglaAdministrada:
        antes = self._regla(codigo)
        if antes.activa is activa:
            return antes
        guardada = replace(antes, activa=activa, actualizado_en=datetime.now(UTC))
        self._reglas[codigo] = guardada
        accion = AccionHistorial.ACTIVAR if activa else AccionHistorial.DESACTIVAR
        self._registrar(codigo, accion, instantanea(antes), instantanea(guardada), usuario)
        self._al_cambiar()
        return guardada

    def activar_regla(self, codigo: str, *, usuario: str | None = None) -> ReglaAdministrada:
        return self._cambiar_estado(codigo, True, usuario)

    def desactivar_regla(self, codigo: str, *, usuario: str | None = None) -> ReglaAdministrada:
        return self._cambiar_estado(codigo, False, usuario)

    def historial(self, codigo: str) -> list[EntradaHistorial]:
        self._regla(codigo)
        return [e for e in self._historial if e.regla_codigo == codigo]
