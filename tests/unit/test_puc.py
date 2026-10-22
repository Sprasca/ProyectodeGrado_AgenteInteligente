import pytest

from agente.domain.enums import Naturaleza
from agente.domain.errors import CuentaNoExisteError
from agente.domain.puc import CatalogoPUC, CuentaPUC, ReglaIngreso, normalizar


def test_catalogo_semilla_carga(catalogo):
    assert catalogo.existe("4135") and catalogo.existe("2408")
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
