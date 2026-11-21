from decimal import Decimal

import pytest
from fabricas import factura

from agente.domain.clasificador import clasificar
from agente.domain.enums import CondicionPago, Naturaleza
from agente.domain.errors import CatalogoInvalidoError, CuentaNoExisteError
from agente.domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso, normalizar

D, C = Naturaleza.DEBITO, Naturaleza.CREDITO


def test_catalogo_semilla_carga(catalogo):
    assert catalogo.existe("4135")
    assert catalogo.resolver("iva")  # p. ej. 241005 según CSV actual
    assert catalogo.resolver("caja") == "1105" or catalogo.existe(catalogo.resolver("caja"))
    assert len(catalogo.reglas) >= 5


def test_cuenta_inexistente(catalogo):
    with pytest.raises(CuentaNoExisteError):
        catalogo.obtener("9999")


def test_regla_con_cuenta_inexistente_falla():
    with pytest.raises(CuentaNoExisteError):
        CatalogoPUC([CuentaPUC("4135", "x", Naturaleza.CREDITO)], [ReglaIngreso("R", "4140", ("a",))])


def test_codigo_no_numerico():
    with pytest.raises(ValueError):
        CuentaPUC("41A5", "x", Naturaleza.CREDITO)


def test_normalizar_quita_tildes():
    assert normalizar("  MERCANCÍA Envío ") == "mercancia envio"


def test_resolver_roles_operativos(catalogo):
    for rol in ("caja", "bancos", "clientes", "iva"):
        codigo = catalogo.resolver(rol)
        assert catalogo.existe(codigo)


# --- Validación del catálogo -------------------------------------------------------


def test_catalogo_real_no_tiene_codigos_repetidos(catalogo):
    # Al cargarse ya se valida; además ninguna cuenta se pierde al indexar por código.
    codigos = [c.codigo for c in catalogo.cuentas]
    assert len(codigos) == len(set(codigos))


def test_codigos_repetidos_lanzan_error():
    cuentas = [
        CuentaPUC("4135", "Comercio", C),
        CuentaPUC("417505", "Descuento A", D),
        CuentaPUC("417505", "Descuento B", D),
    ]
    with pytest.raises(CatalogoInvalidoError, match="repetidos.*417505"):
        CatalogoPUC(cuentas, [])


def test_regla_con_cuenta_que_no_es_de_ingreso_lanza_error():
    # 1105 existe, pero es de clase 1 (activo): una regla de ingreso no puede apuntarle.
    cuentas = [CuentaPUC("1105", "CAJA", D), CuentaPUC("4135", "Comercio", C)]
    with pytest.raises(CatalogoInvalidoError, match="R-CAJA.*1105.*clase 4 con naturaleza CREDITO"):
        CatalogoPUC(cuentas, [ReglaIngreso("R-CAJA", "1105", ("caja",))])


def test_regla_con_cuenta_de_clase_4_debito_lanza_error():
    # 4175 (devoluciones en ventas) es de clase 4 pero DEBITO: tampoco es un ingreso.
    cuentas = [CuentaPUC("4175", "DEVOLUCIONES", D)]
    with pytest.raises(CatalogoInvalidoError, match="R-DEV.*4175"):
        CatalogoPUC(cuentas, [ReglaIngreso("R-DEV", "4175", ("devolucion",))])


def test_regla_con_cuenta_de_ingreso_credito_es_valida():
    catalogo = CatalogoPUC([CuentaPUC("4145", "Transporte", C)], [ReglaIngreso("R", "4145", ("flete",))])
    assert len(catalogo.reglas) == 1


# --- Cuenta de débito en ventas a crédito --------------------------------------------


@pytest.mark.parametrize(
    ("cuenta_ingreso", "esperada"),
    [("4135", "1306"), ("4120", "1306"), ("4145", "1308"), ("4170", "1308"), ("4140", "1308"), ("4155", "1308")],
)
def test_venta_a_credito_elige_clientes_segun_ingreso(catalogo, cuenta_ingreso, esperada):
    assert catalogo.cuenta_debito_venta(a_credito=True, efectivo=False, cuenta_ingreso=cuenta_ingreso) == esperada


def test_venta_de_contado_no_depende_del_ingreso(catalogo):
    for ingreso in ("4135", "4145"):
        assert catalogo.cuenta_debito_venta(a_credito=False, efectivo=True, cuenta_ingreso=ingreso) == "1105"
        assert catalogo.cuenta_debito_venta(a_credito=False, efectivo=False, cuenta_ingreso=ingreso) == "1110"


@pytest.mark.parametrize(
    ("descripcion", "cuenta_ingreso", "clientes"),
    [
        ("Venta de mercancía varios productos", "4135", "1306"),
        ("Servicio de flete Bogotá-Cartagena", "4145", "1308"),
        ("Reparación de equipo de cómputo", "4170", "1308"),
    ],
)
def test_asiento_de_venta_a_credito_usa_la_cuenta_de_clientes_correcta(catalogo, descripcion, cuenta_ingreso, clientes):
    f = factura(descripciones=(descripcion,), condicion=CondicionPago.CREDITO, medio=None)
    resultado = clasificar(f, catalogo, Decimal("0.80"))
    assert resultado.asiento is not None
    debitos = [p for p in resultado.asiento.partidas if p.naturaleza is D]
    assert [p.cuenta for p in debitos] == [clientes]
    assert cuenta_ingreso in {p.cuenta for p in resultado.asiento.partidas if p.naturaleza is C}
