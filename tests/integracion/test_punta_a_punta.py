"""De punta a punta con PostgreSQL real y FUENTE_CATALOGO=bd, sin reemplazar dependencias de la API.

El contador desactiva una regla vía API y la siguiente factura que dependía de ella queda SIN_REGLA,
sin reiniciar la API. La regla es propia de la prueba (palabra clave única) y se borra al final.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agente.api.main import app
from agente.application.clasificar_con_nlp import invalidar_catalogo
from agente.config import get_settings

pytestmark = pytest.mark.integracion

XML = (Path(__file__).resolve().parents[1] / "fixtures" / "factura_comercio_contado.xml").read_text(encoding="utf-8")


@pytest.fixture
def cliente_bd(monkeypatch, fabrica_bd):
    monkeypatch.setenv("FUENTE_CATALOGO", "bd")
    get_settings.cache_clear()
    invalidar_catalogo()
    app.dependency_overrides.clear()
    yield TestClient(app)
    get_settings.cache_clear()
    invalidar_catalogo()


def _clasificar(cliente, descripcion):
    xml = XML.replace("Venta de mercancía varios productos", descripcion)
    r = cliente.post(
        "/facturas/clasificar",
        files={"archivo": ("factura.xml", xml.encode("utf-8"), "application/xml")},
        params={"usar_nlp": "false", "guardar": "false"},
    )
    assert r.status_code == 200, r.text
    (linea,) = r.json()["lineas"]
    return linea


def test_desactivar_una_regla_via_api_deja_la_siguiente_factura_sin_regla(cliente_bd, aislamiento):
    assert get_settings().fuente_catalogo == "bd"
    codigo, palabra = aislamiento.codigo(), aislamiento.palabra()
    descripcion = f"Servicio de {palabra}"

    # Sin la regla, nada reconoce la palabra.
    assert _clasificar(cliente_bd, descripcion)["estado"] == "SIN_REGLA"

    creada = cliente_bd.post(
        "/reglas", json={"codigo": codigo, "cuenta_ingreso": "4145", "palabras_clave": [palabra]}
    )
    assert creada.status_code == 201, creada.text

    linea = _clasificar(cliente_bd, descripcion)
    assert (linea["estado"], linea["regla"], linea["cuenta_ingreso"]) == ("CLASIFICADA", codigo, "4145")

    assert cliente_bd.post(f"/reglas/{codigo}/desactivar").status_code == 200

    linea = _clasificar(cliente_bd, descripcion)
    assert linea["estado"] == "SIN_REGLA"
    assert linea["regla"] is None

    historial = cliente_bd.get(f"/reglas/{codigo}/historial").json()
    assert [e["accion"] for e in historial] == ["CREAR", "DESACTIVAR"]
