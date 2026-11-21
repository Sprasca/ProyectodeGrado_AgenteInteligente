"""Reglas de ingreso que administra el contador (RF09): validación antes de guardarlas."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .enums import Naturaleza
from .errors import ConflictoReglaError, ReglaInvalidaError
from .puc import CuentaPUC, ReglaIngreso, normalizar

# Coincide con regla_clasificacion.codigo (String(60)).
LONGITUD_MAXIMA_CODIGO = 60


@dataclass(frozen=True)
class ReglaAdministrada:
    """Regla tal como está guardada: con su estado y sus fechas."""

    codigo: str
    cuenta_ingreso: str
    palabras_clave: tuple[str, ...]
    descripcion: str = ""
    activa: bool = True
    creado_en: datetime | None = None
    actualizado_en: datetime | None = None

    def como_regla_ingreso(self) -> ReglaIngreso:
        return ReglaIngreso(self.codigo, self.cuenta_ingreso, self.palabras_clave, self.descripcion)


@dataclass(frozen=True)
class ConflictoPalabra:
    """Una palabra clave que ya usa OTRA regla activa con una cuenta distinta."""

    palabra: str
    regla: str
    cuenta: str

    def __str__(self) -> str:
        return f"La palabra clave «{self.palabra}» ya está en la regla {self.regla} (cuenta {self.cuenta})."


@dataclass(frozen=True)
class ValidacionRegla:
    regla: ReglaIngreso  # normalizada: código sin espacios, palabras normalizadas y sin repetidos
    errores: tuple[str, ...]
    advertencias: tuple[ConflictoPalabra, ...]

    @property
    def es_valida(self) -> bool:
        return not self.errores


def normalizar_palabras_clave(palabras: Iterable[str]) -> tuple[str, ...]:
    """normalizar() a cada palabra, sin vacías y sin repetidos (conserva el orden)."""
    return tuple(dict.fromkeys(p for p in (normalizar(x) for x in palabras) if p))


def validar_regla(
    regla: ReglaIngreso,
    cuentas: Iterable[CuentaPUC],
    existentes: Iterable[ReglaAdministrada],
    *,
    es_nueva: bool,
) -> ValidacionRegla:
    """
    Valida una regla contra el catálogo y las reglas ya guardadas.

    Errores (impiden guardar): código vacío, demasiado largo o repetido (solo al crear),
    sin palabras clave, cuenta inexistente o que no es de clase 4 con naturaleza CREDITO.

    Advertencias (RF09): cada palabra clave que ya exista en OTRA regla activa que apunte
    a una cuenta distinta, con la regla y la cuenta con las que choca.
    """
    codigo = regla.codigo.strip()
    palabras = normalizar_palabras_clave(regla.palabras_clave)
    normalizada = ReglaIngreso(codigo, regla.cuenta_ingreso.strip(), palabras, regla.descripcion.strip())
    por_codigo = {c.codigo: c for c in cuentas}
    existentes = list(existentes)

    errores: list[str] = []
    if not codigo:
        errores.append("El código de la regla no puede estar vacío.")
    elif len(codigo) > LONGITUD_MAXIMA_CODIGO:
        errores.append(f"El código de la regla no puede tener más de {LONGITUD_MAXIMA_CODIGO} caracteres.")
    elif es_nueva and any(e.codigo == codigo for e in existentes):
        errores.append(f"Ya existe una regla con el código {codigo}.")

    if not palabras:
        errores.append("La regla debe tener al menos una palabra clave.")

    cuenta = por_codigo.get(normalizada.cuenta_ingreso)
    if cuenta is None:
        errores.append(f"La cuenta {normalizada.cuenta_ingreso} no existe en el catálogo.")
    elif cuenta.clase != "4" or cuenta.naturaleza is not Naturaleza.CREDITO:
        errores.append(
            f"La cuenta {cuenta.codigo} ({cuenta.nombre}, clase {cuenta.clase}, {cuenta.naturaleza}) no es "
            "de ingreso: una regla debe apuntar a una cuenta de clase 4 con naturaleza CREDITO."
        )

    advertencias = tuple(
        ConflictoPalabra(palabra, otra.codigo, otra.cuenta_ingreso)
        for palabra in palabras
        for otra in existentes
        if otra.activa
        and otra.codigo != codigo
        and otra.cuenta_ingreso != normalizada.cuenta_ingreso
        and palabra in otra.palabras_clave
    )
    return ValidacionRegla(normalizada, tuple(errores), advertencias)


def exigir_guardable(validacion: ValidacionRegla, confirmar: bool) -> None:
    """Errores → ReglaInvalidaError. Advertencias sin confirmar → ConflictoReglaError (no se guarda)."""
    if validacion.errores:
        raise ReglaInvalidaError(validacion.errores)
    if validacion.advertencias and not confirmar:
        raise ConflictoReglaError(validacion.advertencias)


# Coinciden con cuenta_puc.codigo (String(10)) y cuenta_puc.nombre (String(200)).
LONGITUD_MAXIMA_CODIGO_CUENTA = 10
LONGITUD_MAXIMA_NOMBRE_CUENTA = 200


def validar_cuenta(codigo: str, nombre: str) -> tuple[str, ...]:
    """Errores de una cuenta nueva (el código repetido lo detecta el repositorio)."""
    errores: list[str] = []
    if not codigo.isdigit():
        errores.append(f"El código de cuenta {codigo!r} debe tener solo dígitos.")
    elif len(codigo) > LONGITUD_MAXIMA_CODIGO_CUENTA:
        errores.append(f"El código de cuenta no puede tener más de {LONGITUD_MAXIMA_CODIGO_CUENTA} dígitos.")
    if not nombre.strip():
        errores.append("El nombre de la cuenta no puede estar vacío.")
    elif len(nombre.strip()) > LONGITUD_MAXIMA_NOMBRE_CUENTA:
        errores.append(f"El nombre de la cuenta no puede tener más de {LONGITUD_MAXIMA_NOMBRE_CUENTA} caracteres.")
    return tuple(errores)
