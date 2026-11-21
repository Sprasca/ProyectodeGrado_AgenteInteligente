"""Fixtures compartidos por las pruebas de integración del catálogo (PostgreSQL real)."""

import random
import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from agente.config import get_settings
from agente.infrastructure.db.models import CuentaPUCModel, HistorialReglaModel, ReglaClasificacionModel
from agente.infrastructure.db.session import crear_sessionmaker


@pytest.fixture(scope="session")
def fabrica_bd():
    """Sesiones contra DATABASE_URL. Salta con un mensaje claro si no hay conexión."""
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


class Aislamiento:
    """Códigos únicos por prueba (reglas, cuentas, palabras) y limpieza de todo lo creado con ellos."""

    def __init__(self, fabrica) -> None:
        self._fabrica = fabrica
        self.prefijo_regla = f"R-IT{uuid.uuid4().hex[:8]}-"
        with fabrica() as sesion:
            while True:
                self.prefijo_cuenta = f"4{random.randint(0, 999_999):06d}"  # + 3 dígitos = 10
                ocupado = sesion.scalar(
                    select(func.count())
                    .select_from(CuentaPUCModel)
                    .where(CuentaPUCModel.codigo.like(f"{self.prefijo_cuenta}%"))
                )
                if not ocupado:
                    break
        self.prefijo_palabra = f"kw{uuid.uuid4().hex[:8]}"
        self._n = 0

    def _siguiente(self) -> int:
        self._n += 1
        return self._n

    def codigo(self) -> str:
        return f"{self.prefijo_regla}{self._siguiente()}"

    def cuenta(self) -> str:
        return f"{self.prefijo_cuenta}{self._siguiente():03d}"

    def palabra(self) -> str:
        return f"{self.prefijo_palabra}x{self._siguiente()}"

    def limpiar(self) -> None:
        with self._fabrica() as sesion, sesion.begin():
            sesion.execute(
                delete(HistorialReglaModel).where(HistorialReglaModel.regla_codigo.like(f"{self.prefijo_regla}%"))
            )
            sesion.execute(
                delete(ReglaClasificacionModel).where(ReglaClasificacionModel.codigo.like(f"{self.prefijo_regla}%"))
            )
            sesion.execute(delete(CuentaPUCModel).where(CuentaPUCModel.codigo.like(f"{self.prefijo_cuenta}%")))


@pytest.fixture
def aislamiento(fabrica_bd):
    datos = Aislamiento(fabrica_bd)
    yield datos
    datos.limpiar()
