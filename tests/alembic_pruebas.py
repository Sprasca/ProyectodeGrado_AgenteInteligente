"""Configuración de Alembic y utilidades SQL para las pruebas que migran una base temporal."""

import uuid
from datetime import date
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

RAIZ = Path(__file__).resolve().parents[1]


def configuracion_alembic() -> Config:
    configuracion = Config(str(RAIZ / "alembic.ini"))
    configuracion.set_main_option("script_location", str(RAIZ / "migrations"))
    return configuracion


def insertar_reporte(
    sesion: Session,
    tipo: str,
    numero: int,
    cupo: int,
    estado: str = "CONSOLIDADO",
    jornada: date = date(2026, 11, 24),
) -> None:
    """Inserta un reporte con el esquema 0010 sin pasar por el repositorio (para probar las restricciones)."""
    sesion.execute(
        text(
            "INSERT INTO reporte_diario (id, fecha_jornada, tipo, numero, cupo, corte, estado, numero_transacciones, "
            "total_debito, total_credito, valor_total, movimientos, subtotales) VALUES (:id, :jornada, :tipo, "
            ":numero, :cupo, now(), :estado, 0, 0, 0, 0, '[]', '[]')"
        ),
        {"id": uuid.uuid4(), "jornada": jornada, "tipo": tipo, "numero": numero, "cupo": cupo, "estado": estado},
    )
