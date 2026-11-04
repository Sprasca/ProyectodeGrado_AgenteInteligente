from decimal import Decimal

import pytest

from agente.domain.clasificador import clasificar
from agente.domain.enums import CondicionPago, EstadoTransaccion, Naturaleza
from agente.domain.factura import Factura, Impuesto, LineaFactura, Tercero


def _factura_basica(
    descripcion: str = "Venta de mercancía varios productos",
    condicion: CondicionPago = CondicionPago.CONTADO,
    medio: str | None = "10",
) -> Factura:
    return Factura(
        numero="FV-TEST",
        fecha=__import__("datetime").date(2025, 3, 15),
        tipo_documento="01",
        moneda="COP",
        emisor=Tercero("900123456", "Emisor"),
        adquirente=Tercero("800987654", "Cliente"),
        condicion_pago=condicion,
        medio_pago=medio,
        lineas=(
            LineaFactura("1", descripcion, Decimal("2"), Decimal("100000")),
        ),
        impuestos=(Impuesto("01", Decimal("19000"), Decimal("19")),),
        subtotal_lineas=Decimal("100000"),
        descuento_global=Decimal("0"),
        cargos_globales=Decimal("0"),
        base_gravable=Decimal("100000"),
        total_con_impuestos=Decimal("119000"),
        total_a_pagar=Decimal("119000"),
    )


def test_clasifica_comercio_contado_efectivo(catalogo):
    resultado = clasificar(_factura_basica(), catalogo, Decimal("0.80"))
    assert resultado.estado is EstadoTransaccion.APROBADA_AUTOMATICA
    assert resultado.confianza == Decimal("1.000")
    assert "R-4135-COMERCIO" in resultado.reglas_aplicadas
    assert resultado.asiento is not None
    assert resultado.asiento.total_debitos == Decimal("119000")

    cuentas = {p.cuenta: p for p in resultado.asiento.partidas}
    assert cuentas["1105"].naturaleza is Naturaleza.DEBITO
    assert cuentas["4135"].naturaleza is Naturaleza.CREDITO
    assert cuentas["2408"].naturaleza is Naturaleza.CREDITO


def test_clasifica_credito_usa_clientes(catalogo):
    f = _factura_basica(condicion=CondicionPago.CREDITO, medio=None)
    resultado = clasificar(f, catalogo)
    assert resultado.asiento is not None
    assert any(p.cuenta == "1305" for p in resultado.asiento.partidas)


def test_sin_regla_coincide_alerta(catalogo):
    f = _factura_basica(descripcion="Servicio de consultoría espacial cuántica")
    resultado = clasificar(f, catalogo)
    assert resultado.estado is EstadoTransaccion.EN_ALERTA
    assert resultado.asiento is None
    assert any("ninguna regla" in m.lower() or "ninguna línea" in m.lower() for m in resultado.motivos_alerta)


def test_tipo_no_venta_alerta(catalogo):
    f = _factura_basica()
    # Forzamos un tipo que no es venta
    object.__setattr__(f, "tipo_documento", "91")  # type: ignore[misc]
    # Como es frozen, mejor recreamos
    f2 = Factura(
        numero=f.numero,
        fecha=f.fecha,
        tipo_documento="91",
        moneda=f.moneda,
        emisor=f.emisor,
        adquirente=f.adquirente,
        condicion_pago=f.condicion_pago,
        medio_pago=f.medio_pago,
        lineas=f.lineas,
        impuestos=f.impuestos,
        subtotal_lineas=f.subtotal_lineas,
        descuento_global=f.descuento_global,
        cargos_globales=f.cargos_globales,
        base_gravable=f.base_gravable,
        total_con_impuestos=f.total_con_impuestos,
        total_a_pagar=f.total_a_pagar,
    )
    resultado = clasificar(f2, catalogo)
    assert resultado.estado is EstadoTransaccion.EN_ALERTA
    assert resultado.asiento is None