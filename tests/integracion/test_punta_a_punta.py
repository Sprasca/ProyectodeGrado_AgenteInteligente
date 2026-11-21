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
from agente.domain.enums import Rol

pytestmark = pytest.mark.integracion

XML = (Path(__file__).resolve().parents[1] / "fixtures" / "factura_comercio_contado.xml").read_text(encoding="utf-8")


@pytest.fixture
def cliente_bd(monkeypatch, fabrica_bd, usuarios_bd):
    monkeypatch.setenv("FUENTE_CATALOGO", "bd")
    get_settings.cache_clear()
    invalidar_catalogo()
    app.dependency_overrides.clear()
    yield TestClient(app)
    get_settings.cache_clear()
    invalidar_catalogo()


def _clasificar(cliente, descripcion, encabezados):
    xml = XML.replace("Venta de mercancía varios productos", descripcion)
    r = cliente.post(
        "/facturas/clasificar",
        files={"archivo": ("factura.xml", xml.encode("utf-8"), "application/xml")},
        params={"usar_nlp": "false", "guardar": "false"},
        headers=encabezados,
    )
    assert r.status_code == 200, r.text
    (linea,) = r.json()["lineas"]
    return linea


def test_desactivar_una_regla_via_api_deja_la_siguiente_factura_sin_regla(cliente_bd, aislamiento, como_bd):
    assert get_settings().fuente_catalogo == "bd"
    contador, auxiliar = como_bd(Rol.CONTADOR), como_bd(Rol.AUXILIAR)
    codigo, palabra = aislamiento.codigo(), aislamiento.palabra()
    descripcion = f"Servicio de {palabra}"

    # Sin la regla, nada reconoce la palabra.
    assert _clasificar(cliente_bd, descripcion, auxiliar)["estado"] == "SIN_REGLA"

    creada = cliente_bd.post(
        "/reglas", json={"codigo": codigo, "cuenta_ingreso": "4145", "palabras_clave": [palabra]}, headers=contador
    )
    assert creada.status_code == 201, creada.text

    linea = _clasificar(cliente_bd, descripcion, auxiliar)
    assert (linea["estado"], linea["regla"], linea["cuenta_ingreso"]) == ("CLASIFICADA", codigo, "4145")

    assert cliente_bd.post(f"/reglas/{codigo}/desactivar", headers=contador).status_code == 200

    linea = _clasificar(cliente_bd, descripcion, auxiliar)
    assert linea["estado"] == "SIN_REGLA"
    assert linea["regla"] is None

    historial = cliente_bd.get(f"/reglas/{codigo}/historial", headers=contador).json()
    assert [e["accion"] for e in historial] == ["CREAR", "DESACTIVAR"]


def test_flujo_completo_con_usuarios_reales(cliente_bd, usuarios_bd, como_bd):
    """Inicio de sesión real, carga con documento y CUFE, eventos con los usuarios reales y rechazo."""
    import hashlib
    import uuid

    from autenticacion_pruebas import CONTRASENA
    from fabricas import cufe_unico, xml_factura

    auxiliar = usuarios_bd[Rol.AUXILIAR]
    contador = como_bd(Rol.CONTADOR)

    # Inicio de sesión con la contraseña real (Argon2 en la base).
    login = cliente_bd.post("/auth/token", data={"username": auxiliar.nombre_usuario, "password": CONTRASENA})
    assert login.status_code == 200, login.text
    token_auxiliar = {"Authorization": f"Bearer {login.json()['access_token']}"}

    cufe = cufe_unico()
    contenido = xml_factura(f"ITEST-E2E-{uuid.uuid4().hex[:8]}", cufe)
    subida = cliente_bd.post(
        "/facturas/clasificar",
        files={"archivo": ("factura-e2e.xml", contenido, "application/xml")},
        params={"usar_nlp": "false"},
        headers=token_auxiliar,
    )
    assert subida.status_code == 200, subida.text
    id_ = subida.json()["id"]

    # Documento original idéntico, con su SHA-256.
    documento = cliente_bd.get(f"/transacciones/{id_}/documento", headers=contador)
    assert documento.content == contenido
    assert documento.headers["x-contenido-sha256"] == hashlib.sha256(contenido).hexdigest()

    # El mismo CUFE con otro número es la misma factura.
    repetida = cliente_bd.post(
        "/facturas/clasificar",
        files={"archivo": ("otra.xml", xml_factura(f"ITEST-E2E-{uuid.uuid4().hex[:8]}", cufe), "application/xml")},
        params={"usar_nlp": "false"},
        headers=token_auxiliar,
    )
    assert repetida.status_code == 409

    # El ADMIN no puede resolver transacciones; el CONTADOR rechaza con motivo.
    motivo = {"motivo": "Factura de prueba de integración"}
    rechazo_admin = cliente_bd.post(f"/transacciones/{id_}/rechazar", json=motivo, headers=como_bd(Rol.ADMIN))
    assert rechazo_admin.status_code == 403
    rechazo = cliente_bd.post(f"/transacciones/{id_}/rechazar", json=motivo, headers=contador)
    assert rechazo.status_code == 200
    assert rechazo.json()["estado"] == "RECHAZADA"

    eventos = cliente_bd.get(f"/transacciones/{id_}/eventos", headers=contador).json()
    assert [(e["tipo"], e["usuario_id"]) for e in eventos] == [
        ("RECIBIDA", auxiliar.id),
        ("CLASIFICADA", None),
        ("RECHAZADA", usuarios_bd[Rol.CONTADOR].id),
    ]
    assert eventos[0]["despues"]["documento"]["sha256"] == hashlib.sha256(contenido).hexdigest()
    assert eventos[0]["despues"]["cufe"] == cufe
    assert eventos[-1]["motivo"] == "Factura de prueba de integración"


def test_historial_de_reglas_con_el_usuario_real(cliente_bd, aislamiento, usuarios_bd, como_bd):
    codigo = aislamiento.codigo()
    contador = como_bd(Rol.CONTADOR)
    cuerpo = {"codigo": codigo, "cuenta_ingreso": "4145", "palabras_clave": [aislamiento.palabra()]}
    assert cliente_bd.post("/reglas", json=cuerpo, headers=contador).status_code == 201
    assert cliente_bd.get(f"/reglas/{codigo}", headers=contador).json()["origen"] == "contador"
    (entrada,) = cliente_bd.get(f"/reglas/{codigo}/historial", headers=contador).json()
    assert entrada["usuario"] == usuarios_bd[Rol.CONTADOR].nombre_usuario

