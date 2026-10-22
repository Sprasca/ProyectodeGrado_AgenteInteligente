from dataclasses import dataclass
from decimal import Decimal

from .enums import Naturaleza
from .errors import AsientoInvalidoError


@dataclass(frozen=True)
class Partida:
    cuenta: str
    naturaleza: Naturaleza
    valor: Decimal
    descripcion: str = ""


@dataclass(frozen=True)
class AsientoContable:
    """Asiento de partida doble. Solo puede existir si cuadra."""

    partidas: tuple[Partida, ...]

    def __post_init__(self) -> None:
        if len(self.partidas) < 2:
            raise AsientoInvalidoError("Un asiento requiere al menos dos partidas.")
        for p in self.partidas:
            if p.valor <= 0:
                raise AsientoInvalidoError(
                    f"La partida de la cuenta {p.cuenta} tiene un valor no positivo ({p.valor})."
                )
        if self.total_debitos != self.total_creditos:
            raise AsientoInvalidoError(
                f"Asiento descuadrado: débitos {self.total_debitos} != créditos {self.total_creditos}."
            )

    @property
    def total_debitos(self) -> Decimal:
        return sum((p.valor for p in self.partidas if p.naturaleza is Naturaleza.DEBITO), Decimal(0))

    @property
    def total_creditos(self) -> Decimal:
        return sum((p.valor for p in self.partidas if p.naturaleza is Naturaleza.CREDITO), Decimal(0))