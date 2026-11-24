"""Contrato de RepositorioReportes sobre la implementación en memoria."""

import itertools

import pytest
from contrato_reportes import ContratoReportes
from repositorio_memoria import RepositorioMemoria
from repositorio_reportes_memoria import RepositorioReportesMemoria


@pytest.fixture
def transacciones(catalogo):
    return RepositorioMemoria(catalogo)


@pytest.fixture
def reportes(transacciones):
    return RepositorioReportesMemoria(transacciones)


@pytest.fixture
def id_usuario():
    return 1


@pytest.fixture
def nuevo_numero():
    contador = itertools.count(1)
    return lambda: f"FV-REP-{next(contador)}"


class TestRepositorioReportesMemoria(ContratoReportes):
    pass
