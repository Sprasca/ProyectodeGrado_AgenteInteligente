"""Configuración de Alembic para las pruebas que migran una base temporal."""

from pathlib import Path

from alembic.config import Config

RAIZ = Path(__file__).resolve().parents[1]


def configuracion_alembic() -> Config:
    configuracion = Config(str(RAIZ / "alembic.ini"))
    configuracion.set_main_option("script_location", str(RAIZ / "migrations"))
    return configuracion
