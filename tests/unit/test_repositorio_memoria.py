import itertools

import pytest
from contrato_repositorio import ContratoRepositorio
from fabricas import catalogo_con_cuenta_no_sembrada
from repositorio_memoria import RepositorioMemoria


@pytest.fixture
def repo(catalogo):
    return RepositorioMemoria(catalogo)


@pytest.fixture
def repo_extendido(catalogo):
    """Catálogo con una cuenta nueva que la "base" (cuentas_en_bd) aún no tiene."""
    return RepositorioMemoria(
        catalogo_con_cuenta_no_sembrada(catalogo), cuentas_en_bd={c.codigo for c in catalogo.cuentas}
    )


@pytest.fixture
def nuevo_numero():
    contador = itertools.count(1)
    return lambda: f"MEM-{next(contador)}"


class TestRepositorioMemoria(ContratoRepositorio):
    pass
