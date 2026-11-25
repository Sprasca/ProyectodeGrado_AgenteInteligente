"""Transacción de base de datos compartida por los repositorios."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker


class RepositorioNoDisponibleError(Exception):
    """No hay conexión con la base de datos."""


@contextmanager
def transaccion_bd(fabrica: sessionmaker[Session]) -> Iterator[Session]:
    """Una sesión en una transacción de base de datos; sin conexión → RepositorioNoDisponibleError."""
    try:
        with fabrica() as sesion, sesion.begin():
            yield sesion
    except (OperationalError, InterfaceError) as exc:
        raise RepositorioNoDisponibleError(
            "No hay conexión con PostgreSQL. Verifique que la base esté levantada "
            "(docker compose up -d db) y que DATABASE_URL sea correcta."
        ) from exc
