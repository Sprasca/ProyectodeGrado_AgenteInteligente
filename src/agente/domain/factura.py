from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .enums import CondicionPago

# InvoiceTypeCode de la DIAN que el agente trata como venta (RN01).
TIPOS_VENTA = frozenset({"01", "02", "03"})
CODIGO_IVA = "01"
MEDIO_PAGO_EFECTIVO = "10"


@dataclass(frozen=True)
class Tercero:
    nit: str
    nombre: str


@dataclass(frozen=True)
class LineaFactura:
    id: str
    descripcion: str
    cantidad: Decimal
    valor_linea: Decimal  # LineExtensionAmount: ya neto de descuentos de línea


@dataclass(frozen=True)
class Impuesto:
    codigo: str  # 01 IVA, 03 ICA, 04 INC ...
    valor: Decimal
    porcentaje: Decimal | None = None


@dataclass(frozen=True)
class Factura:
    numero: str
    fecha: date
    tipo_documento: str
    moneda: str
    emisor: Tercero
    adquirente: Tercero
    condicion_pago: CondicionPago
    medio_pago: str | None
    lineas: tuple[LineaFactura, ...]
    impuestos: tuple[Impuesto, ...]
    subtotal_lineas: Decimal  # LegalMonetaryTotal/LineExtensionAmount
    descuento_global: Decimal
    cargos_globales: Decimal
    base_gravable: Decimal  # TaxExclusiveAmount
    total_con_impuestos: Decimal  # TaxInclusiveAmount
    total_a_pagar: Decimal  # PayableAmount
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def es_venta(self) -> bool:
        return self.tipo_documento in TIPOS_VENTA
