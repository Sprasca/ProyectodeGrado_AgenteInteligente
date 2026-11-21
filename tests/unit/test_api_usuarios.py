"""/usuarios (solo ADMIN): crear, listar, cambiar rol y desactivar; nunca expone el hash."""

import pytest
from autenticacion_pruebas import CONTRASENA, USUARIOS, encabezados

from agente.domain.enums import Rol

ADMIN = encabezados(Rol.ADMIN)
NUEVO = {"nombre_usuario": "Nueva.Auxiliar", "nombre_completo": "Nueva Auxiliar", "contrasena": "clave-segura-1"}


def test_listar_usuarios_sin_hash(entorno_api):
    r = entorno_api.cliente.get("/usuarios", headers=ADMIN)
    assert r.status_code == 200
    usuarios = r.json()
    assert [u["nombre_usuario"] for u in usuarios] == ["contadora", "auxiliar", "administrador"]
    for u in usuarios:
        assert set(u) == {"id", "nombre_usuario", "nombre_completo", "rol", "activo", "creado_en"}
    assert CONTRASENA not in r.text and "argon2" not in r.text


def test_crear_usuario_201_y_puede_iniciar_sesion(entorno_api):
    r = entorno_api.cliente.post("/usuarios", json={**NUEVO, "rol": "AUXILIAR"}, headers=ADMIN)
    assert r.status_code == 201
    creado = r.json()
    assert (creado["nombre_usuario"], creado["rol"], creado["activo"]) == ("nueva.auxiliar", "AUXILIAR", True)
    assert "contrasena" not in creado and "hash_contrasena" not in creado
    token = entorno_api.cliente.post("/auth/token", data={"username": "nueva.auxiliar", "password": "clave-segura-1"})
    assert token.status_code == 200


def test_crear_usuario_repetido_409(entorno_api):
    cuerpo = {**NUEVO, "nombre_usuario": "CONTADORA", "rol": "CONTADOR"}
    r = entorno_api.cliente.post("/usuarios", json=cuerpo, headers=ADMIN)
    assert r.status_code == 409


@pytest.mark.parametrize(
    ("cambios", "fragmento"),
    [
        ({"contrasena": "corta"}, "al menos 8"),
        ({"nombre_usuario": "a b"}, "nombre de usuario"),
        ({"nombre_usuario": "x"}, "nombre de usuario"),
        ({"nombre_completo": "  "}, "nombre completo"),
    ],
)
def test_crear_usuario_invalido_422(entorno_api, cambios, fragmento):
    r = entorno_api.cliente.post("/usuarios", json={**NUEVO, "rol": "AUXILIAR", **cambios}, headers=ADMIN)
    assert r.status_code == 422
    assert any(fragmento in e for e in r.json()["errores"])


@pytest.mark.parametrize(
    "cuerpo", [{**NUEVO, "rol": "JEFE"}, {**NUEVO}, {**NUEVO, "rol": "AUXILIAR", "activo": False}]
)
def test_crear_usuario_cuerpo_mal_formado_422(entorno_api, cuerpo):
    assert entorno_api.cliente.post("/usuarios", json=cuerpo, headers=ADMIN).status_code == 422


def test_cambiar_rol(entorno_api):
    id_auxiliar = USUARIOS[Rol.AUXILIAR].id
    r = entorno_api.cliente.put(f"/usuarios/{id_auxiliar}", json={"rol": "CONTADOR"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["rol"] == "CONTADOR"
    # Con el mismo token, ahora puede lo que puede un CONTADOR.
    desactivar = entorno_api.cliente.post("/reglas/R-4135-COMERCIO/desactivar", headers=encabezados(Rol.AUXILIAR))
    assert desactivar.status_code == 200


def test_desactivar_usuario(entorno_api):
    id_contadora = USUARIOS[Rol.CONTADOR].id
    r = entorno_api.cliente.put(f"/usuarios/{id_contadora}", json={"activo": False}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["activo"] is False
    assert entorno_api.cliente.get("/reglas", headers=encabezados(Rol.CONTADOR)).status_code == 401
    login = entorno_api.cliente.post("/auth/token", data={"username": "contadora", "password": CONTRASENA})
    assert login.status_code == 401


def test_cambiar_nombre_completo(entorno_api):
    r = entorno_api.cliente.put("/usuarios/2", json={"nombre_completo": "  Otro Nombre  "}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["nombre_completo"] == "Otro Nombre"


@pytest.mark.parametrize("cambio", [{"activo": False}, {"rol": "CONTADOR"}])
def test_no_se_puede_dejar_el_sistema_sin_admin_activo(entorno_api, cambio):
    r = entorno_api.cliente.put(f"/usuarios/{USUARIOS[Rol.ADMIN].id}", json=cambio, headers=ADMIN)
    assert r.status_code == 409
    assert "único ADMIN" in r.json()["detail"]


def test_con_otro_admin_activo_si_se_puede(entorno_api):
    otro = entorno_api.cliente.post("/usuarios", json={**NUEVO, "rol": "ADMIN"}, headers=ADMIN).json()
    r = entorno_api.cliente.put(f"/usuarios/{USUARIOS[Rol.ADMIN].id}", json={"activo": False}, headers=ADMIN)
    assert r.status_code == 200
    assert otro["rol"] == "ADMIN"


def test_actualizar_usuario_inexistente_404(entorno_api):
    assert entorno_api.cliente.put("/usuarios/999", json={"activo": False}, headers=ADMIN).status_code == 404


@pytest.mark.parametrize("cuerpo", [{}, {"contrasena": "nueva-clave-1"}, {"rol": "JEFE"}])
def test_actualizar_sin_cambios_validos_422(entorno_api, cuerpo):
    assert entorno_api.cliente.put("/usuarios/2", json=cuerpo, headers=ADMIN).status_code == 422
