from dataclasses import dataclass
from decimal import Decimal

from .asiento import AsientoContable
from .enums import EstadoLinea, EstadoTransaccion
from .factura import Factura


@dataclass(frozen=True)
class ClasificacionLinea:
    """Cómo se emparejó una línea de la factura con las reglas y con qué evidencia."""

    id_linea: str
    descripcion: str
    estado: EstadoLinea
    cuenta_ingreso: str | None
    regla: str | None
    palabras_encontradas: tuple[str, ...]
    cuentas_candidatas: tuple[str, ...]


@dataclass(frozen=True)
class ResultadoClasificacion:
    """Resultado del motor de reglas sobre una factura."""

    factura: Factura
    asiento: AsientoContable | None
    estado: EstadoTransaccion
    confianza: Decimal
    reglas_aplicadas: tuple[str, ...]
    motivos_alerta: tuple[str, ...]
    lineas: tuple[ClasificacionLinea, ...] = ()
