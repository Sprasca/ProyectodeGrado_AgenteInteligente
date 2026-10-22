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
    """Asocia palabras clave de la descripción de una línea con una cuenta de ingreso (grupo 4)."""

    codigo: str
    cuenta_ingreso: str
    palabras_clave: tuple[str, ...]
    descripcion: str = ""


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto)
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    return sin_tildes.lower().strip()


class CatalogoPUC:
    """Catálogo en memoria. Se alimenta del CSV semilla o de la base de datos."""

    def __init__(self, cuentas: list[CuentaPUC], reglas: list[ReglaIngreso]) -> None:
        self._cuentas = {c.codigo: c for c in cuentas}
        self._reglas = list(reglas)
        for r in self._reglas:
            self.obtener(r.cuenta_ingreso)  # una regla no puede apuntar a una cuenta inexistente

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

    @classmethod
    def desde_csv(cls, directorio: Path) -> "CatalogoPUC":
        with (directorio / "cuentas_ventas.csv").open(encoding="utf-8", newline="") as f:
            cuentas = [
                CuentaPUC(r["codigo"], r["nombre"], Naturaleza(r["naturaleza"])) for r in csv.DictReader(f)
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