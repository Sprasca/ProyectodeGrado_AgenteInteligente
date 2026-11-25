"""Horario de la jornada (lo define el contador), en PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ...domain.jornada import Horario, validar_cambio_horario
from .horario import a_horario, horario_vigente
from .models import HorarioJornadaModel
from .transacciones import transaccion_bd


class RepositorioHorario(Protocol):
    def vigente(self) -> Horario: ...

    def cambiar(
        self, apertura: time, cierre: time, *, usuario_id: int | None, ahora: datetime | None = None
    ) -> Horario:
        """
        Fija un horario nuevo y deja registro de quién y cuándo. HorarioInvalidoError si la apertura no es
        anterior al cierre; TransicionInvalidaError si el cambio movería la jornada en curso.
        """
        ...

    def historial(self) -> list[Horario]:
        """Todos los horarios, del más reciente al más antiguo."""
        ...


class RepositorioHorarioSQL:
    def __init__(self, fabrica_sesiones: sessionmaker[Session]) -> None:
        self._fabrica = fabrica_sesiones

    def vigente(self) -> Horario:
        with transaccion_bd(self._fabrica) as sesion:
            return horario_vigente(sesion)

    def cambiar(
        self, apertura: time, cierre: time, *, usuario_id: int | None, ahora: datetime | None = None
    ) -> Horario:
        ahora = ahora or datetime.now(UTC)
        with transaccion_bd(self._fabrica) as sesion:
            # Dos cambios simultáneos se validan uno después del otro.
            sesion.execute(select(HorarioJornadaModel.id).with_for_update())
            validar_cambio_horario(horario_vigente(sesion), apertura, cierre, ahora)
            fila = HorarioJornadaModel(
                hora_apertura=apertura, hora_cierre=cierre, usuario_id=usuario_id, creado_en=ahora
            )
            sesion.add(fila)
            sesion.flush()
            return a_horario(fila)

    def historial(self) -> list[Horario]:
        with transaccion_bd(self._fabrica) as sesion:
            filas = sesion.scalars(select(HorarioJornadaModel).order_by(HorarioJornadaModel.id.desc()))
            return [a_horario(f) for f in filas]
