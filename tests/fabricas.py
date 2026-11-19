"""Constructores de facturas y resultados para las pruebas (sin red ni base de datos)."""

from datetime import date
from decimal import Decimal

from agente.domain.asiento import Partida
from agente.domain.clasificador import clasificar
from agente.domain.enums import CondicionPago, EstadoTransaccion, Naturaleza
from agente.domain.factura import Factura, Impuesto, LineaFactura, Tercero
from agente.domain.puc import CatalogoPUC
from agente.domain.resultado import ResultadoClasificacion

VALOR_LINEA = Decimal("100000")


def factura(
    numero: str = "FV-TEST",
    descripciones: tuple[str, ...] = ("Venta de mercancía varios productos",),
    condicion: CondicionPago = CondicionPago.CONTADO,
    medio: str | None = "10",
) -> Factura:
    """Factura de venta con una línea de 100.000 por descripción e IVA del 19 %."""
    subtotal = VALOR_LINEA * len(descripciones)
    iva = (subtotal * Decimal("0.19")).quantize(Decimal("0.01"))
    return Factura(
        numero=numero,
        fecha=date(2025, 3, 15),
        tipo_documento="01",
        moneda="COP",
        emisor=Tercero("900123456", "Emisor"),
        adquirente=Tercero("800987654", "Cliente"),
        condicion_pago=condicion,
        medio_pago=medio,
        lineas=tuple(
            LineaFactura(str(i), d, Decimal("1"), VALOR_LINEA) for i, d in enumerate(descripciones, start=1)
        ),
        impuestos=(Impuesto("01", iva, Decimal("19")),),
        subtotal_lineas=subtotal,
        descuento_global=Decimal("0"),
        cargos_globales=Decimal("0"),
        base_gravable=subtotal,
        total_con_impuestos=subtotal + iva,
        total_a_pagar=subtotal + iva,
    )


def resultado_aprobado(catalogo: CatalogoPUC, numero: str) -> ResultadoClasificacion:
    """APROBADA_AUTOMATICA con asiento."""
    r = clasificar(factura(numero), catalogo, Decimal("0.80"))
    assert r.estado is EstadoTransaccion.APROBADA_AUTOMATICA and r.asiento is not None
    return r


def resultado_alerta_con_asiento(catalogo: CatalogoPUC, numero: str) -> ResultadoClasificacion:
    """EN_ALERTA por umbral inalcanzable, pero con asiento propuesto."""
    r = clasificar(factura(numero), catalogo, Decimal("1.01"))
    assert r.estado is EstadoTransaccion.EN_ALERTA and r.asiento is not None
    return r


def resultado_alerta_sin_asiento(catalogo: CatalogoPUC, numero: str) -> ResultadoClasificacion:
    """EN_ALERTA sin asiento (ninguna regla coincide)."""
    r = clasificar(factura(numero, ("Concepto genérico no catalogado XYZ-999",)), catalogo)
    assert r.estado is EstadoTransaccion.EN_ALERTA and r.asiento is None
    return r


def partidas_correctas(catalogo: CatalogoPUC) -> list[Partida]:
    """Asiento cuadrado de 119.000 con cuentas del catálogo actual."""
    return [
        Partida(catalogo.resolver("caja"), Naturaleza.DEBITO, Decimal("119000.00"), "Cobro"),
        Partida("4135", Naturaleza.CREDITO, Decimal("100000.00"), "Ingreso corregido"),
        Partida(catalogo.cuenta_iva(), Naturaleza.CREDITO, Decimal("19000.00"), "IVA"),
    ]
