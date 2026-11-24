"""Fixtures compartidos por las pruebas de integración del catálogo (PostgreSQL real)."""

import random
import uuid

import pytest
from autenticacion_pruebas import CONTRASENA
from limpieza_bd import borrar_transacciones
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from agente.config import get_settings
from agente.infrastructure.db.models import (
    CuentaPUCModel,
    EventoTransaccionModel,
    HistorialReglaModel,
    ReglaClasificacionModel,
    TransaccionModel,
)
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


@pytest.fixture
def usuarios_bd(fabrica_bd):
    """Un usuario real por rol en PostgreSQL (nombres únicos); se borran al terminar la prueba."""
    from agente.domain.enums import Rol
    from agente.infrastructure.db.models import UsuarioModel
    from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

    repo = RepositorioUsuariosSQL(fabrica_bd)
    sufijo = uuid.uuid4().hex[:8]
    creados = {
        rol: repo.crear(f"it-{rol.value.lower()}-{sufijo}", f"{rol.value.title()} de integración", CONTRASENA, rol)
        for rol in Rol
    }
    yield creados
    ids = [u.id for u in creados.values()]
    with fabrica_bd() as sesion, sesion.begin():
        # Primero las transacciones con eventos de estos usuarios (sus llaves foráneas no tienen cascada).
        con_eventos = select(EventoTransaccionModel.transaccion_id).where(EventoTransaccionModel.usuario_id.in_(ids))
        borrar_transacciones(sesion, TransaccionModel.id.in_(con_eventos))
        sesion.execute(delete(UsuarioModel).where(UsuarioModel.id.in_(ids)))


@pytest.fixture
def como_bd(usuarios_bd):
    """como_bd(Rol.CONTADOR) → encabezados con un token del usuario real de ese rol."""
    from agente.api.autenticacion import crear_token

    return lambda rol: {"Authorization": f"Bearer {crear_token(usuarios_bd[rol])}"}


@pytest.fixture
def base_temporal(fabrica_bd, monkeypatch):
    """
    Una base vacía en el mismo servidor; DATABASE_URL apunta a ella durante la prueba y se elimina al final.
    La base de DATABASE_URL (con los datos de quien ejecuta las pruebas) no se toca.
    """
    from sqlalchemy import create_engine

    url = make_url(get_settings().database_url)
    nombre = f"agente_prueba_{uuid.uuid4().hex[:12]}"
    administracion = create_engine(url, isolation_level="AUTOCOMMIT")
    with administracion.connect() as conexion:
        conexion.execute(text(f'CREATE DATABASE "{nombre}"'))
    temporal = url.set(database=nombre)
    monkeypatch.setenv("DATABASE_URL", temporal.render_as_string(hide_password=False))
    get_settings.cache_clear()
    motor = create_engine(temporal)
    try:
        yield motor
    finally:
        motor.dispose()
        get_settings.cache_clear()
        with administracion.connect() as conexion:
            conexion.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        administracion.dispose()
