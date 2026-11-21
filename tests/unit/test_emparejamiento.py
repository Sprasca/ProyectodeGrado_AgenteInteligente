"""Emparejamiento de reglas por palabra completa, puntaje y ambigüedad (catálogo actual de data/puc)."""

from decimal import Decimal

from fabricas import factura

from agente.domain.clasificador import clasificar, emparejar_linea
from agente.domain.enums import EstadoLinea, EstadoTransaccion
from agente.domain.factura import LineaFactura
from agente.domain.puc import coincide_clave, palabras


def _emparejar(descripcion, catalogo):
    return emparejar_linea(LineaFactura("1", descripcion, Decimal("1"), Decimal("100")), catalogo.reglas)


# --- Casos exigidos -----------------------------------------------------------------


def test_venta_de_mercancia_clasifica_4135(catalogo):
    r = _emparejar("Venta de mercancía varios productos", catalogo)
    assert r.estado is EstadoLinea.CLASIFICADA
    assert r.cuenta_ingreso == "4135"
    assert r.regla == "R-4135-COMERCIO"
    assert r.palabras_encontradas == ("mercancia", "producto")


def test_servicio_de_flete_clasifica_4145(catalogo):
    r = _emparejar("Servicio de flete Bogotá-Cartagena", catalogo)
    assert r.estado is EstadoLinea.CLASIFICADA
    assert r.cuenta_ingreso == "4145"
    assert r.palabras_encontradas == ("flete",)


def test_venta_de_servicio_de_transporte_clasifica_4145(catalogo):
    # Sin "venta" en R-4135-COMERCIO, la línea ya no es ambigua.
    r = _emparejar("Venta de servicio de transporte", catalogo)
    assert r.estado is EstadoLinea.CLASIFICADA
    assert r.cuenta_ingreso == "4145"
    assert r.palabras_encontradas == ("transporte",)

    resultado = clasificar(factura(descripciones=("Venta de servicio de transporte",)), catalogo, Decimal("0.80"))
    assert resultado.estado is EstadoTransaccion.APROBADA_AUTOMATICA


def test_alquiler_de_transporte_es_ambigua_y_factura_en_alerta(catalogo):
    r = _emparejar("Alquiler de transporte", catalogo)
    assert r.estado is EstadoLinea.AMBIGUA
    assert r.cuenta_ingreso is None
    assert r.regla is None
    assert set(r.cuentas_candidatas) == {"4145", "4155"}
    assert set(r.palabras_encontradas) == {"alquiler", "transporte"}

    resultado = clasificar(factura(descripciones=("Alquiler de transporte",)), catalogo, Decimal("0.80"))
    assert resultado.estado is EstadoTransaccion.EN_ALERTA
    assert resultado.asiento is None
    assert resultado.confianza == Decimal("0")  # la línea AMBIGUA no cuenta como emparejada
    motivo = next(m for m in resultado.motivos_alerta if "ambigua" in m)
    assert "4145" in motivo and "4155" in motivo


def test_mantenimiento_de_oficina_clasifica_4170(catalogo):
    # "oficina" ya no es palabra clave de ninguna regla: solo coincide "mantenimiento".
    r = _emparejar("Mantenimiento de oficina", catalogo)
    assert r.estado is EstadoLinea.CLASIFICADA
    assert r.cuenta_ingreso == "4170"


def test_reparacion_de_repuestos_es_ambigua(catalogo):
    r = _emparejar("Reparación de repuestos", catalogo)
    assert r.estado is EstadoLinea.AMBIGUA
    assert set(r.cuentas_candidatas) == {"4135", "4170"}


def test_local_no_coincide_dentro_de_localidad(catalogo):
    r = _emparejar("Servicio en la localidad de Suba", catalogo)
    assert r.estado is EstadoLinea.SIN_REGLA
    assert r.cuenta_ingreso is None
    assert r.palabras_encontradas == ()
    assert r.cuentas_candidatas == ()


def test_plural_envios_clasifica_4145(catalogo):
    r = _emparejar("Envíos nacionales", catalogo)
    assert r.estado is EstadoLinea.CLASIFICADA
    assert r.cuenta_ingreso == "4145"
    assert r.palabras_encontradas == ("envio",)


def test_factura_con_linea_clasificada_y_sin_regla_queda_en_alerta(catalogo):
    f = factura(descripciones=("Venta de mercancía varios productos", "Servicio en la localidad de Suba"))
    resultado = clasificar(f, catalogo, Decimal("0.80"))

    assert resultado.estado is EstadoTransaccion.EN_ALERTA
    assert resultado.confianza == Decimal("0.500")
    assert len(resultado.lineas) == 2

    clasificada, sin_regla = resultado.lineas
    assert clasificada.id_linea == "1"
    assert clasificada.estado is EstadoLinea.CLASIFICADA
    assert clasificada.cuenta_ingreso == "4135"
    assert clasificada.palabras_encontradas == ("mercancia", "producto")

    assert sin_regla.id_linea == "2"
    assert sin_regla.estado is EstadoLinea.SIN_REGLA
    assert sin_regla.palabras_encontradas == ()


# --- Reglas de coincidencia -----------------------------------------------------------


def test_palabras_normaliza_y_separa():
    assert palabras("Servicio de flete Bogotá-Cartagena") == ("servicio", "de", "flete", "bogota", "cartagena")


def test_plural_con_es():
    assert coincide_clave("local", palabras("Arriendo de locales"))
    assert not coincide_clave("local", palabras("Localidad"))


def test_clave_de_varias_palabras_requiere_palabras_consecutivas():
    assert coincide_clave("asesoria empresarial", palabras("Asesoría empresarial mensual"))
    assert coincide_clave("asesoria empresarial", palabras("Asesoria empresariales"))  # plural en la última
    assert not coincide_clave("asesoria empresarial", palabras("Asesoría contable empresarial"))
    assert not coincide_clave("asesoria empresarial", palabras("Asesorías empresariales"))  # plural solo en la última


def test_puntaje_mayor_gana_sin_ambiguedad(catalogo):
    # 4155: alquiler + arriendo (2 palabras) vence a 4145: transporte (1 palabra)
    r = _emparejar("Alquiler y arriendo de transporte", catalogo)
    assert r.estado is EstadoLinea.CLASIFICADA
    assert r.cuenta_ingreso == "4155"
    assert set(r.palabras_encontradas) == {"alquiler", "arriendo"}


def test_palabra_repetida_cuenta_una_sola_vez(catalogo):
    # "transporte transporte" sigue siendo 1 palabra clave distinta: empata con reparación → AMBIGUA
    r = _emparejar("Transporte transporte y reparación", catalogo)
    assert r.estado is EstadoLinea.AMBIGUA
    assert set(r.cuentas_candidatas) == {"4145", "4170"}
