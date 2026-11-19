import itertools

import pytest
from contrato_repositorio import ContratoRepositorio
from repositorio_memoria import RepositorioMemoria


@pytest.fixture
def repo(catalogo):
    return RepositorioMemoria(catalogo)


@pytest.fixture
def nuevo_numero():
    contador = itertools.count(1)
    return lambda: f"MEM-{next(contador)}"


class TestRepositorioMemoria(ContratoRepositorio):
    pass
