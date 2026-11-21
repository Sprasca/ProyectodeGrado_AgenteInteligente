"""Mismas pruebas de contrato que RepositorioCatalogoMemoria, contra PostgreSQL real.

Requisitos: base levantada, migrada (0004) y sembrada. Ejecutar con: uv run pytest -m integracion
"""

import pytest
from contrato_catalogo import ContratoCatalogo

from agente.infrastructure.db.catalogo import RepositorioCatalogoSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def cambios():
    return []


@pytest.fixture
def repo_catalogo(fabrica_bd, aislamiento, cambios):
    return RepositorioCatalogoSQL(fabrica_bd, al_cambiar=lambda: cambios.append(1))


@pytest.fixture
def nuevo_codigo(aislamiento):
    return aislamiento.codigo


@pytest.fixture
def nueva_cuenta(aislamiento):
    return aislamiento.cuenta


@pytest.fixture
def palabra(aislamiento):
    return aislamiento.palabra


class TestRepositorioCatalogoSQL(ContratoCatalogo):
    pass
