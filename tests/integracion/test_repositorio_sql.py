"""Mismas pruebas de contrato que RepositorioMemoria, contra PostgreSQL real.

Requisitos: docker compose up -d db, uv run alembic upgrade head, uv run python scripts/sembrar_puc.py
Ejecutar con: uv run pytest -m integracion
"""

import itertools
import uuid

import pytest
from contrato_repositorio import ContratoRepositorio
from fabricas import CUENTA_NO_SEMBRADA, catalogo_con_cuenta_no_sembrada
from limpieza_bd import borrar_transacciones
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from agente.config import get_settings
from agente.infrastructure.db.models import CuentaPUCModel, TransaccionModel
from agente.infrastructure.db.session import crear_sessionmaker
from agente.infrastructure.db.transacciones import RepositorioSQL

pytestmark = pytest.mark.integracion


@pytest.fixture(scope="module")
def fabrica_sesiones():
    url = get_settings().database_url
    fabrica = crear_sessionmaker(url, connect_timeout=3)
    try:
        with fabrica() as sesion:
            sesion.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        visible = make_url(url).render_as_string(hide_password=True)
        pytest.skip(
            f"Sin conexión a PostgreSQL en DATABASE_URL={visible} ({type(exc).__name__}). "
            "Levante la base con 'docker compose up -d db' y migre con 'uv run alembic upgrade head'."
        )
    with fabrica() as sesion:
        if not sesion.scalar(select(func.count()).select_from(CuentaPUCModel)):
            pytest.fail("La tabla cuenta_puc está vacía: ejecute 'uv run python scripts/sembrar_puc.py'.")
    yield fabrica
    fabrica.kw["bind"].dispose()


@pytest.fixture
def nuevo_numero(fabrica_sesiones):
    """Números de factura únicos por prueba; al terminar borra todo lo creado con ese prefijo."""
    prefijo = f"ITEST-{uuid.uuid4().hex[:12]}-"
    contador = itertools.count(1)
    yield lambda: f"{prefijo}{next(contador)}"
    with fabrica_sesiones() as sesion, sesion.begin():
        # Partidas en cascada; eventos y documento se borran antes (no tienen cascada).
        borrar_transacciones(sesion, TransaccionModel.numero_factura.like(f"{prefijo}%"))


class _RepositorioRastreado(RepositorioSQL):
    """Anota los ids guardados para borrarlos aunque el número no use el prefijo de la prueba."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.creados: list[uuid.UUID] = []

    def guardar(self, resultado, **opciones):
        id_ = super().guardar(resultado, **opciones)
        self.creados.append(id_)
        return id_


@pytest.fixture
def crear_repo(fabrica_sesiones, nuevo_numero):
    """Crea repositorios rastreados; al terminar la prueba borra todo lo que guardaron."""
    creados: list[_RepositorioRastreado] = []

    def crear(catalogo):
        repositorio = _RepositorioRastreado(fabrica_sesiones, catalogo)
        creados.append(repositorio)
        return repositorio

    yield crear
    ids = [id_ for r in creados for id_ in r.creados]
    if ids:
        with fabrica_sesiones() as sesion, sesion.begin():
            borrar_transacciones(sesion, TransaccionModel.id.in_(ids))


@pytest.fixture
def repo(crear_repo, catalogo):
    return crear_repo(catalogo)


@pytest.fixture
def repo_extendido(crear_repo, catalogo, fabrica_sesiones):
    """Catálogo con una cuenta que no está en cuenta_puc: la base la rechaza por llave foránea."""
    with fabrica_sesiones() as sesion:
        if sesion.get(CuentaPUCModel, CUENTA_NO_SEMBRADA) is not None:
            pytest.fail(f"La cuenta de prueba {CUENTA_NO_SEMBRADA} no debería existir en cuenta_puc.")
    return crear_repo(catalogo_con_cuenta_no_sembrada(catalogo))


class TestRepositorioSQL(ContratoRepositorio):
    pass


@pytest.fixture
def id_usuario(usuarios_bd):
    """Un usuario real (los eventos lo referencian con llave foránea)."""
    from agente.domain.enums import Rol

    return usuarios_bd[Rol.CONTADOR].id
