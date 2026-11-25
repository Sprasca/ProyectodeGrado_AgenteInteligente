"""Horario de la jornada (lo define el contador) y alertas técnicas, en PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import TipoAlertaTecnica
from ...domain.jornada import Horario, validar_cambio_horario
from .horario import a_horario, horario_vigente
from .models import AlertaTecnicaModel, HorarioJornadaModel
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


# --- Alertas técnicas (la Fase 5 las convertirá en notificaciones) ------------------------------------------


@dataclass(frozen=True)
class AlertaTecnica:
    id: int
    tipo: TipoAlertaTecnica
    fecha_jornada: date | None
    mensaje: str
    datos: dict[str, Any] | None
    creado_en: datetime | None
    notificada_en: datetime | None = None


class RepositorioAlertas(Protocol):
    def registrar(
        self, tipo: TipoAlertaTecnica, fecha_jornada: date | None, mensaje: str, datos: dict[str, Any] | None = None
    ) -> bool:
        """Registra la alerta. False si ya había una del mismo tipo para esa jornada (no la duplica)."""
        ...

    def existe(self, tipo: TipoAlertaTecnica, fecha_jornada: date) -> bool: ...

    def listar(self) -> list[AlertaTecnica]:
        """De la más reciente a la más antigua."""
        ...


class RepositorioAlertasSQL:
    def __init__(self, fabrica_sesiones: sessionmaker[Session]) -> None:
        self._fabrica = fabrica_sesiones

    def registrar(
        self, tipo: TipoAlertaTecnica, fecha_jornada: date | None, mensaje: str, datos: dict[str, Any] | None = None
    ) -> bool:
        with transaccion_bd(self._fabrica) as sesion:
            insertada = sesion.scalar(
                insert(AlertaTecnicaModel)
                .values(tipo=tipo.value, fecha_jornada=fecha_jornada, mensaje=mensaje, datos=datos)
                .on_conflict_do_nothing(constraint="uq_alerta_tecnica_tipo_jornada")
                .returning(AlertaTecnicaModel.id)
            )
            return insertada is not None

    def existe(self, tipo: TipoAlertaTecnica, fecha_jornada: date) -> bool:
        with transaccion_bd(self._fabrica) as sesion:
            return (
                sesion.scalar(
                    select(AlertaTecnicaModel.id).where(
                        AlertaTecnicaModel.tipo == tipo.value, AlertaTecnicaModel.fecha_jornada == fecha_jornada
                    )
                )
                is not None
            )

    def listar(self) -> list[AlertaTecnica]:
        with transaccion_bd(self._fabrica) as sesion:
            filas = sesion.scalars(
                select(AlertaTecnicaModel).order_by(AlertaTecnicaModel.creado_en.desc(), AlertaTecnicaModel.id.desc())
            )
            return [
                AlertaTecnica(
                    f.id,
                    TipoAlertaTecnica(f.tipo),
                    f.fecha_jornada,
                    f.mensaje,
                    f.datos,
                    f.creado_en,
                    f.notificada_en,
                )
                for f in filas
            ]
