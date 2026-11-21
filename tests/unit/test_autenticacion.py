"""Autenticación: POST /auth/token, 401 sin token o con token inválido, y la API no arranca sin JWT_SECRETO."""

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from autenticacion_pruebas import CONTRASENA, USUARIOS, encabezados, ruta_concreta

from agente.api.autenticacion import crear_token
from agente.api.permisos import PERMISO_POR_RUTA
from agente.config import Settings, get_settings
from agente.domain.enums import Rol

RAIZ = Path(__file__).resolve().parents[2]


def _token(entorno, usuario, contrasena):
    return entorno.cliente.post("/auth/token", data={"username": usuario, "password": contrasena})


# --- POST /auth/token -------------------------------------------------------------------------


def test_emite_token_valido(entorno_api):
    r = _token(entorno_api, "contadora", CONTRASENA)
    assert r.status_code == 200
    datos = r.json()
    assert datos["token_type"] == "bearer"
    assert datos["expires_in"] == 60 * 60
    contenido = jwt.decode(datos["access_token"], get_settings().jwt_secreto, algorithms=["HS256"])
    assert contenido["sub"] == "1"
    assert contenido["exp"] - contenido["iat"] == 60 * 60

    # El token sirve para llamar la API.
    autorizacion = {"Authorization": f"Bearer {datos['access_token']}"}
    assert entorno_api.cliente.get("/reglas", headers=autorizacion).status_code == 200


def test_el_nombre_de_usuario_no_distingue_mayusculas(entorno_api):
    assert _token(entorno_api, "  CONTADORA ", CONTRASENA).status_code == 200


@pytest.mark.parametrize(("usuario", "contrasena"), [("contadora", "otra-clave-123"), ("no-existe", CONTRASENA)])
def test_credenciales_incorrectas_401_sin_revelar_cual_fallo(entorno_api, usuario, contrasena):
    r = _token(entorno_api, usuario, contrasena)
    assert r.status_code == 401
    assert r.json()["detail"] == "Usuario o contraseña incorrectos."
    assert r.headers["www-authenticate"] == "Bearer"


def test_usuario_inactivo_no_obtiene_token(entorno_api):
    entorno_api.usuarios.actualizar(USUARIOS[Rol.CONTADOR].id, activo=False)
    assert _token(entorno_api, "contadora", CONTRASENA).status_code == 401


def test_faltan_campos_del_formulario_422(entorno_api):
    assert entorno_api.cliente.post("/auth/token", data={"username": "contadora"}).status_code == 422


# --- Rutas protegidas: 401 ----------------------------------------------------------------------


def test_rutas_publicas_no_piden_token(entorno_api):
    assert entorno_api.cliente.get("/salud").status_code == 200


@pytest.mark.parametrize(("metodo", "ruta"), sorted(PERMISO_POR_RUTA))
def test_toda_ruta_protegida_sin_token_responde_401(entorno_api, metodo, ruta):
    r = entorno_api.cliente.request(metodo, ruta_concreta(ruta))
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def _llamar(entorno, token):
    return entorno.cliente.get("/reglas", headers={"Authorization": f"Bearer {token}"})


def test_token_vencido_401(entorno_api):
    vencido = crear_token(USUARIOS[Rol.CONTADOR], ahora=datetime.now(UTC) - timedelta(hours=2))
    r = _llamar(entorno_api, vencido)
    assert r.status_code == 401
    assert "expiró" in r.json()["detail"]


def test_token_firmado_con_otro_secreto_401(entorno_api):
    ahora = datetime.now(UTC)
    falso = jwt.encode(
        {"sub": "1", "iat": ahora, "exp": ahora + timedelta(minutes=5)}, "otro-secreto" * 4, algorithm="HS256"
    )
    assert _llamar(entorno_api, falso).status_code == 401


def test_token_sin_vencimiento_401(entorno_api):
    sin_exp = jwt.encode({"sub": "1", "iat": datetime.now(UTC)}, get_settings().jwt_secreto, algorithm="HS256")
    assert _llamar(entorno_api, sin_exp).status_code == 401


def test_token_sin_firma_alg_none_401(entorno_api):
    ahora = datetime.now(UTC)
    sin_firma = jwt.encode({"sub": "1", "iat": ahora, "exp": ahora + timedelta(minutes=5)}, None, algorithm="none")
    assert _llamar(entorno_api, sin_firma).status_code == 401


@pytest.mark.parametrize("valor", ["Bearer", "Bearer basura", "Basic Y29udGFkb3JhOmNsYXZl", "basura"])
def test_encabezado_mal_formado_401(entorno_api, valor):
    assert entorno_api.cliente.get("/reglas", headers={"Authorization": valor}).status_code == 401


def test_token_de_usuario_inexistente_401(entorno_api):
    from agente.domain.usuarios import Usuario

    fantasma = crear_token(Usuario(99, "fantasma", "Fantasma", Rol.CONTADOR))
    assert _llamar(entorno_api, fantasma).status_code == 401


def test_desactivar_un_usuario_invalida_su_token_de_inmediato(entorno_api):
    token = encabezados(Rol.CONTADOR)
    assert entorno_api.cliente.get("/reglas", headers=token).status_code == 200
    entorno_api.usuarios.actualizar(USUARIOS[Rol.CONTADOR].id, activo=False)
    assert entorno_api.cliente.get("/reglas", headers=token).status_code == 401


def test_cambiar_el_rol_tiene_efecto_de_inmediato(entorno_api):
    token = encabezados(Rol.CONTADOR)
    entorno_api.usuarios.actualizar(USUARIOS[Rol.CONTADOR].id, rol=Rol.AUXILIAR)
    r = entorno_api.cliente.post("/reglas/R-4135-COMERCIO/desactivar", headers=token)
    assert r.status_code == 403  # el rol se lee de la base, no del token


# --- Configuración: sin JWT_SECRETO la API no arranca ---------------------------------------------


@pytest.mark.parametrize(("secreto", "fragmento"), [("", "Falta JWT_SECRETO"), ("corto", "al menos 32")])
def test_la_api_no_arranca_sin_un_secreto_valido(tmp_path, secreto, fragmento):
    entorno = {**os.environ, "JWT_SECRETO": secreto, "PYTHONPATH": str(RAIZ / "src")}
    # Directorio vacío: sin .env que pudiera aportar un secreto.
    r = subprocess.run(
        [sys.executable, "-c", "import agente.api.main"], cwd=tmp_path, env=entorno, capture_output=True, text=True
    )
    assert r.returncode != 0
    assert "ConfiguracionInseguraError" in r.stderr
    assert fragmento in r.stderr


def test_la_api_arranca_con_un_secreto_valido(tmp_path):
    entorno = {**os.environ, "JWT_SECRETO": "x" * 32, "PYTHONPATH": str(RAIZ / "src")}
    r = subprocess.run(
        [sys.executable, "-c", "import agente.api.main"], cwd=tmp_path, env=entorno, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("minutos", ["0", "-5", "diez"])
def test_jwt_minutos_invalido(monkeypatch, minutos):
    monkeypatch.setenv("JWT_MINUTOS", minutos)
    with pytest.raises(ValueError, match="JWT_MINUTOS"):
        Settings()


def test_jwt_minutos_por_defecto_60(monkeypatch, tmp_path):
    monkeypatch.delenv("JWT_MINUTOS", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Settings().jwt_minutos == 60
