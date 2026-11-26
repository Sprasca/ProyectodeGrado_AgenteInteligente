"""Envíos por correo contra PostgreSQL, sobre una base temporal, con SMTP "configurado" y un enviador falso."""

import pytest
from autenticacion_pruebas import CONTRASENA
from contrato_notificaciones import ContratoEnviosCorreo, causa
from sqlalchemy import func, select

from agente.config import get_settings
from agente.domain.enums import Rol
from agente.infrastructure.db.models import EnvioCorreoModel
from agente.infrastructure.db.notificaciones import RepositorioNotificacionesSQL
from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def smtp_configurado(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.prueba.invalid")
    monkeypatch.setenv("SMTP_REMITENTE", "agente@empresa.co")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def notificaciones(bd_sembrada, smtp_configurado):
    return RepositorioNotificacionesSQL(bd_sembrada)


@pytest.fixture
def usuarios(bd_sembrada):
    repo = RepositorioUsuariosSQL(bd_sembrada)
    return {
        "contador": repo.crear("contadora", "Contadora", CONTRASENA, Rol.CONTADOR, "contador@empresa.co").id,
        "auxiliar": repo.crear("auxiliar", "Auxiliar", CONTRASENA, Rol.AUXILIAR).id,
        "admin": repo.crear("admin", "Admin", CONTRASENA, Rol.ADMIN, "admin@empresa.co").id,
    }


class TestEnviosCorreoSQL(ContratoEnviosCorreo):
    pass


def test_sin_smtp_no_se_crean_envios(bd_sembrada, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SMTP_HOST", "")
    get_settings.cache_clear()
    try:
        RepositorioUsuariosSQL(bd_sembrada).crear("admin", "Admin", CONTRASENA, Rol.ADMIN, "admin@empresa.co")
        RepositorioNotificacionesSQL(bd_sembrada).registrar(causa())
        with bd_sembrada() as sesion:
            assert sesion.scalar(select(func.count()).select_from(EnvioCorreoModel)) == 0
    finally:
        get_settings.cache_clear()
