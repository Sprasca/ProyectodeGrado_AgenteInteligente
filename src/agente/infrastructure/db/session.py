from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ...config import get_settings


def crear_sessionmaker(url: str | None = None, connect_timeout: int | None = None) -> sessionmaker[Session]:
    """
    connect_timeout (segundos): sin él, psycopg puede esperar indefinidamente si PostgreSQL
    no responde (p. ej. en Windows con la base apagada). La API y las pruebas lo fijan.
    """
    connect_args = {"connect_timeout": connect_timeout} if connect_timeout is not None else {}
    engine = create_engine(url or get_settings().database_url, pool_pre_ping=True, connect_args=connect_args)
    return sessionmaker(engine, expire_on_commit=False)


@lru_cache
def fabrica_compartida() -> sessionmaker[Session]:
    """Fábrica de sesiones única del proceso (API y carga del catálogo). Espera hasta 5 s por la base."""
    return crear_sessionmaker(connect_timeout=5)
