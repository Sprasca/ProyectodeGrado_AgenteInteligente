import itertools

import pytest
from contrato_catalogo import ContratoCatalogo
from repositorio_catalogo_memoria import RepositorioCatalogoMemoria


@pytest.fixture
def cambios():
    return []


@pytest.fixture
def repo_catalogo(catalogo, cambios):
    return RepositorioCatalogoMemoria.desde_catalogo(catalogo, al_cambiar=lambda: cambios.append(1))


@pytest.fixture
def nuevo_codigo():
    contador = itertools.count(1)
    return lambda: f"R-MEM-{next(contador)}"


@pytest.fixture
def nueva_cuenta():
    contador = itertools.count(1)
    return lambda: f"41990{next(contador):03d}"


@pytest.fixture
def palabra():
    contador = itertools.count(1)
    return lambda: f"palabraprueba{next(contador)}"


class TestRepositorioCatalogoMemoria(ContratoCatalogo):
    pass
