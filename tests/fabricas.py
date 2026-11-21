"""Constructores de facturas y resultados para las pruebas (sin red ni base de datos)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from agente.domain.asiento import AsientoContable, Partida
from agente.domain.clasificador import clasificar
from agente.domain.enums import CondicionPago, EstadoTransaccion, Naturaleza
from agente.domain.factura import Factura, Impuesto, LineaFactura, Tercero, numero_provisional
from agente.domain.puc import CatalogoPUC, CuentaPUC
from agente.domain.resultado import ResultadoClasificacion

VALOR_LINEA = Decimal("100000")


def factura(
    numero: str = "FV-TEST",
    descripciones: tuple[str, ...] = ("Venta de mercancía varios productos",),
    condicion: CondicionPago = CondicionPago.CONTADO,
    medio: str | None = "10",
    cufe: str | None = None,
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
        cufe=cufe,
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


def resultado_sin_numero(catalogo: CatalogoPUC) -> ResultadoClasificacion:
    """Factura cuyo documento no trae número: número provisional y EN_ALERTA (con asiento)."""
    r = clasificar(factura(numero_provisional()), catalogo, Decimal("0.80"))
    assert r.estado is EstadoTransaccion.EN_ALERTA and r.asiento is not None
    return r


# Cuenta que existe en un catálogo de prueba pero que nunca se siembra en cuenta_puc.
CUENTA_NO_SEMBRADA = "999901"


def catalogo_con_cuenta_no_sembrada(catalogo: CatalogoPUC) -> CatalogoPUC:
    """Simula que el contador agregó una cuenta a data/puc y no se volvió a sembrar la base."""
    extra = CuentaPUC(CUENTA_NO_SEMBRADA, "Cuenta agregada sin sembrar", Naturaleza.CREDITO)
    return CatalogoPUC([*catalogo.cuentas, extra], list(catalogo.reglas))


def partidas_con_cuenta_no_sembrada(catalogo: CatalogoPUC) -> list[Partida]:
    """Asiento cuadrado cuyo ingreso va a CUENTA_NO_SEMBRADA."""
    return [
        Partida(catalogo.resolver("caja"), Naturaleza.DEBITO, Decimal("119000.00"), "Cobro"),
        Partida(CUENTA_NO_SEMBRADA, Naturaleza.CREDITO, Decimal("100000.00"), "Ingreso"),
        Partida(catalogo.cuenta_iva(), Naturaleza.CREDITO, Decimal("19000.00"), "IVA"),
    ]


def resultado_con_cuenta_no_sembrada(catalogo: CatalogoPUC, numero: str) -> ResultadoClasificacion:
    """Resultado cuyo asiento usa CUENTA_NO_SEMBRADA (p. ej. una regla nueva antes de sembrar)."""
    r = resultado_aprobado(catalogo, numero)
    return replace(r, asiento=AsientoContable(tuple(partidas_con_cuenta_no_sembrada(catalogo))))


def partidas_correctas(catalogo: CatalogoPUC) -> list[Partida]:
    """Asiento cuadrado de 119.000 con cuentas del catálogo actual."""
    return [
        Partida(catalogo.resolver("caja"), Naturaleza.DEBITO, Decimal("119000.00"), "Cobro"),
        Partida("4135", Naturaleza.CREDITO, Decimal("100000.00"), "Ingreso corregido"),
        Partida(catalogo.cuenta_iva(), Naturaleza.CREDITO, Decimal("19000.00"), "IVA"),
    ]


def resultado_con_cufe(catalogo: CatalogoPUC, numero: str, cufe: str) -> ResultadoClasificacion:
    """APROBADA_AUTOMATICA con CUFE."""
    return clasificar(factura(numero, cufe=cufe), catalogo, Decimal("0.80"))


def cufe_unico() -> str:
    """Un CUFE con la forma de los de la DIAN (SHA-384 en hexadecimal, 96 caracteres)."""
    import hashlib
    import uuid

    return hashlib.sha384(uuid.uuid4().bytes).hexdigest()


XML_BASE = (Path(__file__).resolve().parent / "fixtures" / "factura_comercio_contado.xml").read_text(encoding="utf-8")


def xml_factura(numero: str = "FV-1001", cufe: str | None = None, descripcion: str | None = None) -> bytes:
    """El XML de prueba con otro número, con CUFE (cbc:UUID) y, si se pide, otra descripción de línea."""
    uuid_xml = f'<cbc:UUID schemeName="CUFE-SHA384">{cufe}</cbc:UUID>' if cufe else ""
    xml = XML_BASE.replace("<cbc:ID>FV-1001</cbc:ID>", f"<cbc:ID>{numero}</cbc:ID>{uuid_xml}")
    if descripcion is not None:
        xml = xml.replace("Venta de mercancía varios productos", descripcion)
    return xml.encode("utf-8")
