"""Mismas pruebas de contrato que RepositorioMemoria, contra PostgreSQL real.

Requisitos: docker compose up -d db, uv run alembic upgrade head, uv run python scripts/sembrar_puc.py
Ejecutar con: uv run pytest -m integracion
"""

import itertools
import uuid

import pytest
from contrato_repositorio import ContratoRepositorio
from sqlalchemy import delete, func, select, text
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
        # Las partidas se borran en cascada (ON DELETE CASCADE).
        sesion.execute(delete(TransaccionModel).where(TransaccionModel.numero_factura.like(f"{prefijo}%")))


@pytest.fixture
def repo(fabrica_sesiones, catalogo, nuevo_numero):
    return RepositorioSQL(fabrica_sesiones, catalogo)


class TestRepositorioSQL(ContratoRepositorio):
    pass
