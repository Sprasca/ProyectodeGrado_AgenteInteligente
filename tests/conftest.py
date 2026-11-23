import os

import pytest  # type: ignore[reportMissingImports]

from agente.config import get_settings
from agente.domain.enums import Rol
from agente.domain.puc import CatalogoPUC

# Las pruebas unitarias clasifican con los CSV de data/puc: no requieren PostgreSQL.
# La prueba de integración de punta a punta cambia a "bd" de forma explícita.
os.environ["FUENTE_CATALOGO"] = "csv"
# Configuración de seguridad solo para pruebas (no depende del .env de cada quien).
os.environ["JWT_SECRETO"] = "secreto-solo-para-pruebas-0123456789-abcdefghijklmnop"
os.environ["JWT_MINUTOS"] = "60"
os.environ["CORS_ORIGENES"] = "http://permitido.test"
get_settings.cache_clear()


@pytest.fixture
def catalogo() -> CatalogoPUC:
    return CatalogoPUC.desde_csv(get_settings().directorio_puc)


@pytest.fixture
def usuarios_api():
    """Instala en la API los tres usuarios de prueba (CONTADOR, AUXILIAR, ADMIN) en memoria."""
    from autenticacion_pruebas import usuarios_de_prueba

    from agente.api.autenticacion import get_repositorio_usuarios
    from agente.api.main import app

    repo = usuarios_de_prueba()
    app.dependency_overrides[get_repositorio_usuarios] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_repositorio_usuarios, None)


@pytest.fixture
def como():
    """como(Rol.CONTADOR) → encabezados con el token de ese rol."""
    from autenticacion_pruebas import encabezados

    return encabezados


# Atajos para las pruebas existentes de la API.
CONTADOR, AUXILIAR, ADMIN = Rol.CONTADOR, Rol.AUXILIAR, Rol.ADMIN
