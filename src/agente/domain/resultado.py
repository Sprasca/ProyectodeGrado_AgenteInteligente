from dataclasses import dataclass
from decimal import Decimal

from .asiento import AsientoContable
from .enums import EstadoTransaccion
from .factura import Factura


@dataclass(frozen=True)
class ResultadoClasificacion:
    """Resultado del motor de reglas sobre una factura."""

    factura: Factura
    asiento: AsientoContable | None
    estado: EstadoTransaccion
    confianza: Decimal
    reglas_aplicadas: tuple[str, ...]
    motivos_alerta: tuple[str, ...]