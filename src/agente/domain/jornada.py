"""La jornada contable se corta en hora de Colombia (America/Bogota), no en UTC."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

ZONA_COLOMBIA = ZoneInfo("America/Bogota")
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def hora_de_cierre(texto: str) -> time:
    """'18:00' → time(18, 0). ValueError con un mensaje claro si no es HH:MM de 00:00 a 23:59."""
    coincidencia = _HORA.match(texto.strip())
    if coincidencia is None:
        raise ValueError(f"HORA_CIERRE debe tener el formato HH:MM (de 00:00 a 23:59), no {texto!r}.")
    return time(int(coincidencia.group(1)), int(coincidencia.group(2)))


def en_colombia(instante: datetime) -> datetime:
    """El mismo instante en hora de Colombia. Exige un datetime con zona horaria."""
    if instante.tzinfo is None:
        raise ValueError("Se esperaba una fecha y hora con zona horaria.")
    return instante.astimezone(ZONA_COLOMBIA)


def fecha_jornada(instante: datetime) -> date:
    """La fecha de la jornada en Colombia a la que pertenece un instante."""
    return en_colombia(instante).date()


def corte_de_la_jornada(fecha: date, hora: time) -> datetime:
    """El instante (en UTC) del cierre de esa jornada a esa hora de Colombia."""
    return datetime.combine(fecha, hora, tzinfo=ZONA_COLOMBIA).astimezone(UTC)
