"""Contrato de RepositorioReportes sobre la implementación en memoria."""

import itertools
from dataclasses import replace

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


@pytest.fixture
def mover_a_jornada(transacciones):
    def mover(id_, fecha):
        transacciones._por_id[id_] = replace(transacciones._por_id[id_], fecha_jornada=fecha)

    return mover


class TestRepositorioReportesMemoria(ContratoReportes):
    pass
