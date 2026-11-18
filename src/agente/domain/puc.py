"""Catálogo PUC cargado desde data/puc/*.csv.

Las cuentas y reglas las mantiene el contador en los CSV. El dominio no fija
códigos contables: resuelve caja, bancos, clientes e IVA a partir del catálogo
cargado (por código preferido o por nombre).
"""

from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .enums import Naturaleza
from .errors import CuentaNoExisteError


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


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto)
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    return sin_tildes.lower().strip()


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
        self._cuentas = {c.codigo: c for c in cuentas}
        self._reglas = list(reglas)
        for r in self._reglas:
            self.obtener(r.cuenta_ingreso)

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

    def cuenta_debito_venta(self, *, a_credito: bool, efectivo: bool, cuenta_ingreso: str | None = None) -> str:
        """Elige la cuenta de débito según condición de pago y tipo de ingreso."""
        if a_credito:
            if cuenta_ingreso and cuenta_ingreso.startswith("421"):
                return self.resolver("clientes_servicios")
            if cuenta_ingreso and cuenta_ingreso.startswith("413"):
                return self.resolver("clientes_bienes")
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
