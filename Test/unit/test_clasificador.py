from datetime import date
from decimal import Decimal

import pytest

from agente.domain.clasificador import ClasificadorPUC
from agente.domain.enums import CondicionPago, EstadoTransaccion, Naturaleza
from agente.domain.errors import NoEsVentaError
from agente.domain.factura import Factura, Impuesto, LineaFactura, Tercero

D, C = Naturaleza.DEBITO, Naturaleza.CREDITO


def hacer_factura(
    lineas=(("1", "Venta de productos", "100000"),),
    pago=CondicionPago.CONTADO,
    medio="10",
    descuento="0",
    cargos="0",
    codigo_impuesto="01",
    tipo="01",
    total_extra="0",
):
    items = tuple(LineaFactura(i, d, Decimal(1), Decimal(v)) for i, d, v in lineas)
    subtotal = sum((x.valor_linea for x in items), Decimal(0))
    base = subtotal - Decimal(descuento) + Decimal(cargos)
    impuesto = (base * Decimal("0.19")).quantize(Decimal("0.01"))
    total = base + impuesto + Decimal(total_extra)
    return Factura(
        numero="FE-1",
        fecha=date(2026, 10, 1),
        tipo_documento=tipo,
        moneda="COP",
        emisor=Tercero("900000001", "Mi Empresa"),
        adquirente=Tercero("800000002", "Cliente"),
        condicion_pago=pago,
        medio_pago=medio,
        lineas=items,
        impuestos=(Impuesto(codigo_impuesto, impuesto, Decimal(19)),),
        subtotal_lineas=subtotal,
        descuento_global=Decimal(descuento),
        cargos_globales=Decimal(cargos),
        base_gravable=base,
        total_con_impuestos=total,
        total_a_pagar=total,
    )


@pytest.fixture
def clasificador(catalogo):
    return ClasificadorPUC(catalogo)


def cuentas(resultado, naturaleza):
    return {p.cuenta: p.valor for p in resultado.asiento.partidas if p.naturaleza is naturaleza}


def test_venta_contado_efectivo_se_aprueba(clasificador):
    r = clasificador.clasificar(hacer_factura())
    assert r.estado is EstadoTransaccion.APROBADA_AUTOMATICA
    assert cuentas(r, D) == {"1105": Decimal("119000.00")}
    assert cuentas(r, C) == {"4135": Decimal("100000"), "2408": Decimal("19000.00")}
    assert r.asiento.total_debitos == r.asiento.total_creditos
    assert r.confianza >= Decimal("0.80")


def test_venta_a_credito_usa_clientes(clasificador):
    r = clasificador.clasificar(hacer_factura(pago=CondicionPago.CREDITO, medio=None))
    assert "1305" in cuentas(r, D)


def test_pago_no_efectivo_usa_bancos(clasificador):
    r = clasificador.clasificar(hacer_factura(medio="48"))
    assert "1110" in cuentas(r, D)


def test_varias_lineas_generan_ingresos_distintos(clasificador):
    r = clasificador.clasificar(
        hacer_factura(lineas=(("1", "Venta de repuestos", "60000"), ("2", "Servicio de flete", "40000")))
    )
    assert cuentas(r, C)["4135"] == Decimal("60000")
    assert cuentas(r, C)["4145"] == Decimal("40000")
    assert r.estado is EstadoTransaccion.APROBADA_AUTOMATICA


def test_descripcion_sin_regla_queda_en_alerta(clasificador):
    r = clasificador.clasificar(hacer_factura(lineas=(("1", "Concepto varios", "100000"),)))
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.asiento is None
    assert r.confianza == Decimal("0")


def test_descripcion_ambigua_queda_en_alerta_con_asiento(clasificador):
    r = clasificador.clasificar(hacer_factura(lineas=(("1", "Venta de transporte", "100000"),)))
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.asiento is not None
    assert r.confianza == Decimal("0.60")


def test_regla_dominante_mantiene_aprobacion(clasificador):
    r = clasificador.clasificar(hacer_factura(lineas=(("1", "Venta de mercancia con transporte", "100000"),)))
    assert r.estado is EstadoTransaccion.APROBADA_AUTOMATICA
    assert "4135" in cuentas(r, C)


def test_no_coincide_por_subcadena(clasificador):
    r = clasificador.clasificar(hacer_factura(lineas=(("1", "Inventario inicial", "100000"),)))
    assert r.asiento is None


def test_plural_simple_coincide(clasificador):
    r = clasificador.clasificar(hacer_factura(lineas=(("1", "Productos varios", "100000"),)))
    assert "4135" in cuentas(r, C)


def test_documento_no_venta_se_rechaza(clasificador):
    with pytest.raises(NoEsVentaError):
        clasificador.clasificar(hacer_factura(tipo="91"))


def test_impuesto_no_soportado_genera_alerta(clasificador):
    r = clasificador.clasificar(hacer_factura(codigo_impuesto="04"))
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.asiento is None


def test_totales_inconsistentes_generan_alerta(clasificador):
    r = clasificador.clasificar(hacer_factura(total_extra="500"))
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.asiento is None


def test_descuento_global_va_al_debito_en_4175(clasificador):
    r = clasificador.clasificar(hacer_factura(descuento="10000"))
    assert cuentas(r, D)["4175"] == Decimal("10000")
    assert r.asiento.total_debitos == r.asiento.total_creditos
    assert r.estado is EstadoTransaccion.APROBADA_AUTOMATICA


def test_cargos_globales_requieren_revision(clasificador):
    r = clasificador.clasificar(hacer_factura(cargos="5000"))
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.asiento is not None