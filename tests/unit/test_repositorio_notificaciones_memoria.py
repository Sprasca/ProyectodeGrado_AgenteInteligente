"""Contrato de RepositorioNotificaciones sobre la implementación en memoria."""

import pytest
from contrato_notificaciones import ContratoNotificaciones
from repositorio_notificaciones_memoria import RepositorioNotificacionesMemoria

from agente.domain.enums import Rol
from agente.domain.usuarios import Usuario

USUARIOS = [
    Usuario(1, "contadora", "Contadora", Rol.CONTADOR),
    Usuario(2, "auxiliar", "Auxiliar", Rol.AUXILIAR),
    Usuario(3, "admin", "Admin", Rol.ADMIN),
    Usuario(4, "admin-inactivo", "Admin inactivo", Rol.ADMIN, activo=False),
]


@pytest.fixture
def notificaciones():
    return RepositorioNotificacionesMemoria(lambda: USUARIOS)


@pytest.fixture
def usuarios():
    return {"contador": 1, "auxiliar": 2, "admin": 3, "admin_inactivo": 4}


class TestRepositorioNotificacionesMemoria(ContratoNotificaciones):
    pass
