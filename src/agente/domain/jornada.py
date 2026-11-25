"""La jornada contable, en hora de Colombia (America/Bogota), no en UTC.

Una transacción pertenece a la jornada del día si se recibe antes de la HORA_CIERRE de ese día; si se recibe
a esa hora o después, a la del día siguiente. El horario lo define el contador y se guarda en la base
(tabla horario_jornada); la hora de apertura es informativa: no cambia a qué jornada pertenece nada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import HorarioInvalidoError, TransicionInvalidaError

ZONA_COLOMBIA = ZoneInfo("America/Bogota")
APERTURA_INICIAL = time(8, 0)
CIERRE_INICIAL = time(18, 0)
_HORA = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Horario:
    hora_apertura: time
    hora_cierre: time
    # Quién y cuándo lo fijó (None en el valor inicial de la migración).
    actualizado_por: int | None = None
    actualizado_en: datetime | None = None


HORARIO_INICIAL = Horario(APERTURA_INICIAL, CIERRE_INICIAL)


def hora_hhmm(texto: str, nombre: str = "La hora") -> time:
    """'18:00' → time(18, 0). HorarioInvalidoError si no es HH:MM de 00:00 a 23:59."""
    coincidencia = _HORA.match(texto.strip()) if isinstance(texto, str) else None
    if coincidencia is None:
        raise HorarioInvalidoError([f"{nombre} debe tener el formato HH:MM (de 00:00 a 23:59), no {texto!r}."])
    return time(int(coincidencia.group(1)), int(coincidencia.group(2)))


def validar_horario(apertura: time, cierre: time) -> None:
    if apertura >= cierre:
        raise HorarioInvalidoError(
            [f"La hora de apertura ({apertura:%H:%M}) debe ser anterior a la de cierre ({cierre:%H:%M})."]
        )


def en_colombia(instante: datetime) -> datetime:
    """El mismo instante en hora de Colombia. Exige un datetime con zona horaria."""
    if instante.tzinfo is None:
        raise ValueError("Se esperaba una fecha y hora con zona horaria.")
    return instante.astimezone(ZONA_COLOMBIA)


def jornada_de(instante: datetime, hora_cierre: time) -> date:
    """La jornada a la que pertenece lo recibido en ese instante."""
    local = en_colombia(instante)
    return local.date() if local.time() < hora_cierre else local.date() + timedelta(days=1)


def ultima_jornada_terminada(instante: datetime, hora_cierre: time) -> date:
    """La jornada más reciente cuya HORA_CIERRE ya pasó: la anterior a la que está en curso."""
    return jornada_de(instante, hora_cierre) - timedelta(days=1)


def corte_de_la_jornada(fecha: date, hora: time) -> datetime:
    """El instante (en UTC) del cierre de esa jornada a esa hora de Colombia."""
    return datetime.combine(fecha, hora, tzinfo=ZONA_COLOMBIA).astimezone(UTC)


def validar_cambio_horario(actual: Horario, apertura: time, cierre: time, ahora: datetime) -> None:
    """
    El cambio no puede mover la jornada en curso: si la nueva hora de cierre ya pasó hoy (y la actual no), lo
    que se recibe desde ahora saltaría al día siguiente sin que la jornada de hoy se haya cerrado; si la actual
    ya pasó (y la nueva no), se reabriría una jornada que pudo cerrarse. Esas horas se cambian otro día.
    """
    validar_horario(apertura, cierre)
    if jornada_de(ahora, cierre) != jornada_de(ahora, actual.hora_cierre):
        raise TransicionInvalidaError(
            f"La nueva hora de cierre ({cierre:%H:%M}) movería la jornada en curso "
            f"({jornada_de(ahora, actual.hora_cierre)}), porque son las {en_colombia(ahora):%H:%M} y la hora "
            f"de cierre vigente es {actual.hora_cierre:%H:%M}. Elija una hora que esté del mismo lado de la hora "
            "actual o haga el cambio más tarde."
        )
