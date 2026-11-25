"""Lectura del horario vigente dentro de una sesión (la usan la recepción y los reportes)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain.jornada import HORARIO_INICIAL, Horario
from .models import HorarioJornadaModel


def a_horario(fila: HorarioJornadaModel) -> Horario:
    return Horario(fila.hora_apertura, fila.hora_cierre, fila.usuario_id, fila.creado_en)


def horario_vigente(sesion: Session) -> Horario:
    """El horario más reciente; el inicial (08:00 a 18:00) si la tabla estuviera vacía."""
    fila = sesion.scalar(select(HorarioJornadaModel).order_by(HorarioJornadaModel.id.desc()).limit(1))
    return a_horario(fila) if fila is not None else HORARIO_INICIAL
