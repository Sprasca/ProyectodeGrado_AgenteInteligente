"""Contrato de RepositorioReportes contra PostgreSQL, sobre una base temporal (ver bd_sembrada)."""

import itertools

import pytest
from autenticacion_pruebas import CONTRASENA
from contrato_reportes import ContratoReportes

from agente.domain.enums import Rol
from agente.infrastructure.db.reportes import RepositorioReportesSQL
from agente.infrastructure.db.transacciones import RepositorioSQL
from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def transacciones(bd_sembrada, catalogo):
    return RepositorioSQL(bd_sembrada, catalogo)


@pytest.fixture
def reportes(bd_sembrada):
    return RepositorioReportesSQL(bd_sembrada)


@pytest.fixture
def id_usuario(bd_sembrada):
    return RepositorioUsuariosSQL(bd_sembrada).crear("contadora", "Contadora", CONTRASENA, Rol.CONTADOR).id


@pytest.fixture
def nuevo_numero():
    contador = itertools.count(1)
    return lambda: f"FV-REP-{next(contador)}"


class TestRepositorioReportesSQL(ContratoReportes):
    pass
