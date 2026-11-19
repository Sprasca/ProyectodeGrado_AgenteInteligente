"""clasificar_hibrido con sugerir_cuenta reemplazado: nunca se llama a Groq de verdad."""

from decimal import Decimal

import pytest
from fabricas import factura

import agente.application.clasificar_con_nlp as modulo
from agente.domain.enums import EstadoLinea, EstadoTransaccion
from agente.infrastructure.llm.groq_cliente import ErrorGroq, SugerenciaCuenta

UMBRAL = Decimal("0.80")
SIN_REGLA = "Servicio de grúa"  # ninguna palabra clave del catálogo actual
AMBIGUA = "Venta de servicio de transporte"


@pytest.fixture(autouse=True)
def sin_groq_real(monkeypatch):
    def prohibido(*_args, **_kwargs):
        pytest.fail("La prueba intentó llamar a Groq de verdad")

    monkeypatch.setattr(modulo, "sugerir_cuenta", prohibido)


def _con_sugerencia(monkeypatch, respuesta):
    llamadas: list[str] = []

    def falso(descripcion, candidatas):
        llamadas.append(descripcion)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    monkeypatch.setattr(modulo, "sugerir_cuenta", falso)
    return llamadas


def test_sugerencia_con_confianza_suficiente_clasifica_la_linea(monkeypatch, catalogo):
    llamadas = _con_sugerencia(monkeypatch, SugerenciaCuenta(cuenta="421508", confianza=0.95, justificacion="grúa"))
    r = modulo.clasificar_hibrido(factura(descripciones=(SIN_REGLA,)), catalogo, UMBRAL)

    assert llamadas == [SIN_REGLA]
    assert r.estado is EstadoTransaccion.APROBADA_AUTOMATICA
    assert r.asiento is not None
    assert "R-NLP-421508" in r.reglas_aplicadas
    assert r.lineas[0].estado is EstadoLinea.CLASIFICADA
    assert r.lineas[0].cuenta_ingreso == "421508"


def test_sugerencia_igual_al_umbral_clasifica(monkeypatch, catalogo):
    _con_sugerencia(monkeypatch, SugerenciaCuenta(cuenta="421508", confianza=0.80))
    r = modulo.clasificar_hibrido(factura(descripciones=(SIN_REGLA,)), catalogo, UMBRAL)
    assert r.lineas[0].estado is EstadoLinea.CLASIFICADA


def test_sugerencia_bajo_el_umbral_queda_en_alerta(monkeypatch, catalogo):
    llamadas = _con_sugerencia(monkeypatch, SugerenciaCuenta(cuenta="421508", confianza=0.5))
    r = modulo.clasificar_hibrido(factura(descripciones=(SIN_REGLA,)), catalogo, UMBRAL)

    assert llamadas == [SIN_REGLA]
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.asiento is None
    assert r.lineas[0].estado is EstadoLinea.SIN_REGLA


def test_error_de_groq_queda_en_alerta_sin_romper(monkeypatch, catalogo):
    llamadas = _con_sugerencia(monkeypatch, ErrorGroq("sin red"))
    r = modulo.clasificar_hibrido(factura(descripciones=(SIN_REGLA,)), catalogo, UMBRAL)

    assert llamadas == [SIN_REGLA]
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.lineas[0].estado is EstadoLinea.SIN_REGLA


def test_sugerencia_none_queda_en_alerta(monkeypatch, catalogo):
    llamadas = _con_sugerencia(monkeypatch, None)
    r = modulo.clasificar_hibrido(factura(descripciones=(SIN_REGLA,)), catalogo, UMBRAL)

    assert llamadas == [SIN_REGLA]
    assert r.estado is EstadoTransaccion.EN_ALERTA


def test_lineas_ambiguas_no_llaman_a_sugerir_cuenta(monkeypatch, catalogo):
    llamadas = _con_sugerencia(monkeypatch, SugerenciaCuenta(cuenta="421505", confianza=0.99))
    r = modulo.clasificar_hibrido(factura(descripciones=(AMBIGUA,)), catalogo, UMBRAL)

    assert llamadas == []
    assert r.estado is EstadoTransaccion.EN_ALERTA
    assert r.lineas[0].estado is EstadoLinea.AMBIGUA


def test_en_factura_mixta_solo_la_linea_sin_regla_va_al_llm(monkeypatch, catalogo):
    llamadas = _con_sugerencia(monkeypatch, None)
    f = factura(descripciones=(AMBIGUA, SIN_REGLA, "Venta de mercancía"))
    modulo.clasificar_hibrido(f, catalogo, UMBRAL)
    assert llamadas == [SIN_REGLA]


def test_sin_nlp_no_llama_a_sugerir_cuenta(catalogo):
    # El fixture autouse falla la prueba si se intentara llamar a sugerir_cuenta.
    r = modulo.clasificar_hibrido(factura(descripciones=(SIN_REGLA,)), catalogo, UMBRAL, usar_nlp=False)
    assert r.estado is EstadoTransaccion.EN_ALERTA
