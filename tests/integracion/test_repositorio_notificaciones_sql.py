"""Contrato de RepositorioNotificaciones contra PostgreSQL, sobre una base temporal (ver bd_sembrada)."""

import pytest
from autenticacion_pruebas import CONTRASENA
from contrato_notificaciones import ContratoNotificaciones
from sqlalchemy import text

from agente.domain.enums import Rol
from agente.infrastructure.db.notificaciones import RepositorioNotificacionesSQL
from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def notificaciones(bd_sembrada):
    return RepositorioNotificacionesSQL(bd_sembrada)


@pytest.fixture
def usuarios(bd_sembrada):
    repo = RepositorioUsuariosSQL(bd_sembrada)
    ids = {
        "contador": repo.crear("contadora", "Contadora", CONTRASENA, Rol.CONTADOR).id,
        "auxiliar": repo.crear("auxiliar", "Auxiliar", CONTRASENA, Rol.AUXILIAR).id,
        "admin": repo.crear("admin", "Admin", CONTRASENA, Rol.ADMIN).id,
        "admin_inactivo": repo.crear("admin-inactivo", "Admin inactivo", CONTRASENA, Rol.ADMIN).id,
    }
    with bd_sembrada() as sesion, sesion.begin():
        sesion.execute(text("UPDATE usuario SET activo = false WHERE id = :id"), {"id": ids["admin_inactivo"]})
    return ids


class TestRepositorioNotificacionesSQL(ContratoNotificaciones):
    pass
