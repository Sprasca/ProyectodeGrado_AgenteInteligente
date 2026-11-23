"""Mismas pruebas de contrato que RepositorioUsuariosMemoria, contra PostgreSQL real."""

import uuid

import pytest
from contrato_usuarios import ContratoUsuarios
from sqlalchemy import delete

from agente.infrastructure.db.models import UsuarioModel
from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def nombre(fabrica_bd):
    """Nombres únicos por prueba; al terminar borra los usuarios creados con ese prefijo."""
    prefijo = f"it{uuid.uuid4().hex[:8]}-"
    contador = iter(range(1, 1000))
    yield lambda: f"{prefijo}{next(contador)}"
    with fabrica_bd() as sesion, sesion.begin():
        sesion.execute(delete(UsuarioModel).where(UsuarioModel.nombre_usuario.like(f"{prefijo}%")))


@pytest.fixture
def repo_usuarios(fabrica_bd, nombre):
    return RepositorioUsuariosSQL(fabrica_bd)


class TestRepositorioUsuariosSQL(ContratoUsuarios):
    pass


def test_la_contrasena_se_guarda_con_argon2id(fabrica_bd, repo_usuarios, nombre):
    creado = repo_usuarios.crear(nombre(), "Ana", "clave-segura-123", "AUXILIAR")
    with fabrica_bd() as sesion:
        guardado = sesion.get(UsuarioModel, creado.id).hash_contrasena
    assert guardado.startswith("$argon2id$")
    assert "clave-segura-123" not in guardado
