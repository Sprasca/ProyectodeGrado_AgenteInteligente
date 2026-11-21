"""Catálogo PUC cargado desde data/puc/*.csv.

Las cuentas y reglas las mantiene el contador en los CSV. El dominio no fija
códigos contables: resuelve caja, bancos, clientes e IVA a partir del catálogo
cargado (por código preferido o por nombre).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .enums import Naturaleza
from .errors import CatalogoInvalidoError, CuentaNoExisteError


@dataclass(frozen=True)
class CuentaPUC:
    codigo: str
    nombre: str
    naturaleza: Naturaleza

    def __post_init__(self) -> None:
        if not self.codigo.isdigit():
            raise ValueError(f"Código PUC inválido: {self.codigo!r}")

    @property
    def clase(self) -> str:
        return self.codigo[0]


@dataclass(frozen=True)
class ReglaIngreso:
    """Asocia palabras clave de la descripción de una línea con una cuenta de ingreso."""

    codigo: str
    cuenta_ingreso: str
    palabras_clave: tuple[str, ...]
    descripcion: str = ""

    def palabras_coincidentes(self, palabras_texto: tuple[str, ...]) -> tuple[str, ...]:
        """Palabras clave distintas de la regla que aparecen en el texto (ya dividido con palabras())."""
        return tuple(clave for clave in dict.fromkeys(self.palabras_clave) if coincide_clave(clave, palabras_texto))


_RE_PALABRA = re.compile(r"\w+")


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto)
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    return sin_tildes.lower().strip()


def palabras(texto: str) -> tuple[str, ...]:
    """Normaliza el texto y lo divide en palabras (expresión regular \\w+)."""
    return tuple(_RE_PALABRA.findall(normalizar(texto)))


def _misma_palabra(palabra: str, clave: str) -> bool:
    """Igualdad exacta o plural simple (clave + "s" o clave + "es")."""
    return palabra in (clave, clave + "s", clave + "es")


def coincide_clave(clave: str, palabras_texto: tuple[str, ...]) -> bool:
    """
    Una clave de una palabra coincide con una palabra completa del texto (o su plural).
    Una clave de varias palabras coincide si aparecen consecutivas; el plural solo se
    admite en la última. Nunca coincide como subcadena ("local" no está en "localidad").
    """
    partes = palabras(clave)
    if not partes:
        return False
    n = len(partes)
    for i in range(len(palabras_texto) - n + 1):
        ventana = palabras_texto[i : i + n]
        if ventana[:-1] == partes[:-1] and _misma_palabra(ventana[-1], partes[-1]):
            return True
    return False


class CatalogoPUC:
    """Catálogo en memoria alimentado por CSV (o BD). Fuente de verdad: data/puc/."""

    # Preferencias de resolución cuando el contador renumera cuentas.
    # Se prueban en orden; si ninguno existe, se busca por fragmentos del nombre.
    _PREFERENCIAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "caja": (("1105",), ("caja",)),
        "bancos": (("1110",), ("deposito", "banco", "financiera", "cooperativa")),
        "clientes": (("1306", "1308", "1305"), ("cliente", "venta de bienes", "prestacion de servicios")),
        "clientes_bienes": (("1306", "1305"), ("venta de bienes", "cliente")),
        "clientes_servicios": (("1308", "1305"), ("prestacion de servicios", "cliente")),
        "iva": (("241004", "241005", "2408", "2410"), ("impuesto al valor", "iva")),
    }

    def __init__(self, cuentas: list[CuentaPUC], reglas: list[ReglaIngreso]) -> None:
        repetidos = sorted(codigo for codigo, n in Counter(c.codigo for c in cuentas).items() if n > 1)
        if repetidos:
            raise CatalogoInvalidoError(
                f"Códigos de cuenta repetidos en el catálogo: {', '.join(repetidos)}. "
                "Cada código debe aparecer una sola vez en data/puc/cuentas_ventas.csv."
            )
        self._cuentas = {c.codigo: c for c in cuentas}
        self._reglas = list(reglas)
        for r in self._reglas:
            cuenta = self.obtener(r.cuenta_ingreso)
            if cuenta.clase != "4" or cuenta.naturaleza is not Naturaleza.CREDITO:
                raise CatalogoInvalidoError(
                    f"La regla {r.codigo} apunta a la cuenta {cuenta.codigo} ({cuenta.nombre}, "
                    f"clase {cuenta.clase}, {cuenta.naturaleza}); una regla de ingreso debe apuntar "
                    "a una cuenta de clase 4 con naturaleza CREDITO. Revise data/puc/reglas_ingreso.csv."
                )

    @property
    def cuentas(self) -> tuple[CuentaPUC, ...]:
        return tuple(self._cuentas.values())

    @property
    def reglas(self) -> tuple[ReglaIngreso, ...]:
        return tuple(self._reglas)

    def existe(self, codigo: str) -> bool:
        return codigo in self._cuentas

    def obtener(self, codigo: str) -> CuentaPUC:
        try:
            return self._cuentas[codigo]
        except KeyError:
            raise CuentaNoExisteError(f"La cuenta {codigo} no existe en el catálogo PUC.") from None

    def resolver(self, rol: str) -> str:
        """Devuelve el código de cuenta para un rol operativo según el catálogo actual."""
        if rol not in self._PREFERENCIAS:
            raise CuentaNoExisteError(f"Rol contable desconocido: {rol!r}")
        codigos, fragmentos = self._PREFERENCIAS[rol]
        for codigo in codigos:
            if codigo in self._cuentas:
                return codigo
        for cuenta in self._cuentas.values():
            nombre = normalizar(cuenta.nombre)
            if any(frag in nombre for frag in fragmentos):
                return cuenta.codigo
        raise CuentaNoExisteError(
            f"No hay cuenta para el rol {rol!r} en el catálogo actual. "
            f"Actualice data/puc/cuentas_ventas.csv."
        )

    # Ingresos por venta de bienes (agropecuario e industrias manufactureras / comercio).
    _PREFIJOS_INGRESO_BIENES = ("4120", "4135")

    def cuenta_debito_venta(self, *, a_credito: bool, efectivo: bool, cuenta_ingreso: str | None = None) -> str:
        """
        Elige la cuenta de débito según condición de pago y tipo de ingreso.

        A crédito: ingresos 4120 o 4135 → clientes por venta de bienes; cualquier otro
        ingreso de clase 4 → clientes por prestación de servicios.
        """
        if a_credito:
            if cuenta_ingreso and cuenta_ingreso.startswith(self._PREFIJOS_INGRESO_BIENES):
                return self.resolver("clientes_bienes")
            if cuenta_ingreso and cuenta_ingreso.startswith("4"):
                return self.resolver("clientes_servicios")
            return self.resolver("clientes")
        if efectivo:
            return self.resolver("caja")
        return self.resolver("bancos")

    def cuenta_iva(self) -> str:
        return self.resolver("iva")

    @classmethod
    def desde_csv(cls, directorio: Path) -> CatalogoPUC:
        with (directorio / "cuentas_ventas.csv").open(encoding="utf-8", newline="") as f:
            cuentas = [
                CuentaPUC(r["codigo"], r["nombre"], Naturaleza(r["naturaleza"]))
                for r in csv.DictReader(f)
            ]
        with (directorio / "reglas_ingreso.csv").open(encoding="utf-8", newline="") as f:
            reglas = [
                ReglaIngreso(
                    codigo=r["codigo_regla"],
                    cuenta_ingreso=r["cuenta_ingreso"],
                    palabras_clave=tuple(normalizar(p) for p in r["palabras_clave"].split("|") if p),
                    descripcion=r["descripcion"],
                )
                for r in csv.DictReader(f)
            ]
        return cls(cuentas, reglas)
