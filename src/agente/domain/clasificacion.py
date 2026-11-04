from dataclasses import dataclass
from decimal import Decimal

from .asiento import AsientoContable
from .enums import EstadoTransaccion


@dataclass(frozen=True)
class ClasificacionLinea:
    """Cómo se clasificó una línea de la factura y con qué evidencia."""

    id_linea: str
    descripcion: str
    cuenta_ingreso: str | None
    regla: str | None
    palabras_encontradas: tuple[str, ...]
    confianza: Decimal


@dataclass(frozen=True)
class ResultadoClasificacion:
    """Salida del clasificador: asiento propuesto, confianza y motivos de alerta."""

    numero_factura: str
    estado: EstadoTransaccion
    asiento: AsientoContable | None
    confianza: Decimal
    lineas: tuple[ClasificacionLinea, ...]
    motivos_alerta: tuple[str, ...]