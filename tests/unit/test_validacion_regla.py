"""Validación de reglas del contador (RF09): errores que impiden guardar y advertencias de conflicto."""

import pytest

from agente.domain.enums import Naturaleza
from agente.domain.errors import ConflictoReglaError, ReglaInvalidaError
from agente.domain.puc import CuentaPUC, ReglaIngreso
from agente.domain.reglas import (
    ConflictoPalabra,
    ReglaAdministrada,
    exigir_guardable,
    normalizar_palabras_clave,
    validar_cuenta,
    validar_regla,
)

CUENTAS = [
    CuentaPUC("1105", "CAJA", Naturaleza.DEBITO),
    CuentaPUC("4135", "COMERCIO AL POR MAYOR", Naturaleza.CREDITO),
    CuentaPUC("4145", "TRANSPORTE", Naturaleza.CREDITO),
    CuentaPUC("4175", "DEVOLUCIONES", Naturaleza.DEBITO),
    CuentaPUC("2408", "IVA", Naturaleza.CREDITO),
]
EXISTENTES = [
    ReglaAdministrada("R-COMERCIO", "4135", ("mercancia", "producto")),
    ReglaAdministrada("R-TRANSPORTE", "4145", ("flete", "envio")),
    ReglaAdministrada("R-VIEJA", "4145", ("repuesto",), activa=False),
]


def _validar(codigo="R-NUEVA", cuenta="4135", palabras=("abarrote",), es_nueva=True, existentes=EXISTENTES):
    return validar_regla(ReglaIngreso(codigo, cuenta, tuple(palabras)), CUENTAS, existentes, es_nueva=es_nueva)


# --- Normalización ---------------------------------------------------------------------


def test_palabras_se_normalizan_sin_repetidos_ni_vacias():
    assert normalizar_palabras_clave(["  Mercancía ", "MERCANCIA", "", "   ", "Envío"]) == ("mercancia", "envio")


def test_validacion_devuelve_la_regla_normalizada():
    v = _validar(codigo="  R-NUEVA  ", palabras=("Licor ", "licor", "ABARROTE"))
    assert v.es_valida
    assert v.regla.codigo == "R-NUEVA"
    assert v.regla.palabras_clave == ("licor", "abarrote")


# --- Errores ---------------------------------------------------------------------------


def test_regla_valida_sin_errores_ni_advertencias():
    v = _validar()
    assert v.errores == ()
    assert v.advertencias == ()


@pytest.mark.parametrize("codigo", ["", "   "])
def test_codigo_vacio_es_error(codigo):
    assert any("vacío" in e for e in _validar(codigo=codigo).errores)


def test_codigo_demasiado_largo_es_error():
    assert any("60 caracteres" in e for e in _validar(codigo="R" * 61).errores)


def test_codigo_repetido_es_error_al_crear():
    errores = _validar(codigo="R-COMERCIO").errores
    assert errores == ("Ya existe una regla con el código R-COMERCIO.",)


def test_codigo_repetido_incluye_reglas_inactivas():
    assert _validar(codigo="R-VIEJA").errores


def test_al_editar_el_codigo_propio_no_es_repetido():
    assert _validar(codigo="R-COMERCIO", palabras=("mercancia",), es_nueva=False).errores == ()


@pytest.mark.parametrize("palabras", [(), ("", "  ")])
def test_sin_palabras_clave_es_error(palabras):
    assert _validar(palabras=palabras).errores == ("La regla debe tener al menos una palabra clave.",)


def test_cuenta_inexistente_es_error():
    assert _validar(cuenta="9999").errores == ("La cuenta 9999 no existe en el catálogo.",)


@pytest.mark.parametrize("cuenta", ["1105", "4175", "2408"])
def test_cuenta_que_no_es_de_clase_4_credito_es_error(cuenta):
    (error,) = _validar(cuenta=cuenta).errores
    assert cuenta in error
    assert "clase 4 con naturaleza CREDITO" in error


def test_varios_errores_a_la_vez():
    assert len(_validar(codigo="", cuenta="1105", palabras=()).errores) == 3


# --- Advertencias de conflicto (RF09) --------------------------------------------------


def test_palabra_de_otra_regla_activa_con_otra_cuenta_es_advertencia():
    v = _validar(cuenta="4135", palabras=("Flete", "abarrote"))
    assert v.es_valida  # una advertencia no impide guardar
    assert v.advertencias == (ConflictoPalabra("flete", "R-TRANSPORTE", "4145"),)
    assert "R-TRANSPORTE" in str(v.advertencias[0]) and "4145" in str(v.advertencias[0])


def test_cada_palabra_en_conflicto_se_informa():
    v = _validar(cuenta="4135", palabras=("flete", "envios", "envio"))
    assert [a.palabra for a in v.advertencias] == ["flete", "envio"]


def test_misma_cuenta_no_es_conflicto():
    assert _validar(cuenta="4145", palabras=("flete",)).advertencias == ()


def test_regla_inactiva_no_genera_conflicto():
    assert _validar(cuenta="4135", palabras=("repuesto",)).advertencias == ()


def test_al_editar_la_regla_no_choca_consigo_misma():
    v = _validar(codigo="R-TRANSPORTE", cuenta="4135", palabras=("flete",), es_nueva=False)
    assert v.advertencias == ()


def test_conflicto_con_varias_reglas():
    existentes = [*EXISTENTES, ReglaAdministrada("R-OTRA", "4135", ("flete",))]
    v = _validar(cuenta="4145", palabras=("flete",), existentes=existentes, codigo="R-NUEVA")
    assert v.advertencias == (ConflictoPalabra("flete", "R-OTRA", "4135"),)


# --- exigir_guardable y cuentas ------------------------------------------------------------


def test_exigir_guardable():
    with pytest.raises(ReglaInvalidaError) as error:
        exigir_guardable(_validar(cuenta="1105"), confirmar=True)  # confirmar no salta los errores
    assert error.value.errores

    con_conflicto = _validar(palabras=("flete",))
    with pytest.raises(ConflictoReglaError) as conflicto:
        exigir_guardable(con_conflicto, confirmar=False)
    assert conflicto.value.advertencias == con_conflicto.advertencias
    exigir_guardable(con_conflicto, confirmar=True)  # confirmado: se puede guardar


@pytest.mark.parametrize(
    ("codigo", "nombre", "esperado"),
    [
        ("4199", "Otra", ()),
        ("41A9", "Otra", ("solo dígitos",)),
        ("", "Otra", ("solo dígitos",)),
        ("12345678901", "Otra", ("10 dígitos",)),
        ("4199", "  ", ("vacío",)),
        ("4199", "x" * 201, ("200 caracteres",)),
    ],
)
def test_validar_cuenta(codigo, nombre, esperado):
    errores = validar_cuenta(codigo, nombre)
    assert len(errores) == len(esperado)
    for fragmento, error in zip(esperado, errores, strict=True):
        assert fragmento in error
