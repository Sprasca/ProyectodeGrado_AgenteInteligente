"""Entorno de API para las pruebas unitarias: todos los repositorios en memoria y los tres usuarios de prueba."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from repositorio_catalogo_memoria import RepositorioCatalogoMemoria
from repositorio_memoria import RepositorioMemoria

from agente.api.main import app, get_repositorio, get_repositorio_catalogo
from agente.application.clasificar_con_nlp import invalidar_catalogo


@pytest.fixture
def entorno_api(catalogo, usuarios_api):
    transacciones = RepositorioMemoria(catalogo)
    reglas = RepositorioCatalogoMemoria.desde_catalogo(catalogo, al_cambiar=invalidar_catalogo)
    app.dependency_overrides[get_repositorio] = lambda: transacciones
    app.dependency_overrides[get_repositorio_catalogo] = lambda: reglas
    yield SimpleNamespace(cliente=TestClient(app), transacciones=transacciones, reglas=reglas, usuarios=usuarios_api)
    app.dependency_overrides.clear()
