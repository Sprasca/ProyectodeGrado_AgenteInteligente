"""Horario de la jornada y alertas técnicas en memoria. SOLO para pruebas; misma interfaz que las de SQL."""

from datetime import UTC, date, datetime, time
from itertools import count
from typing import Any

from agente.domain.enums import TipoAlertaTecnica
from agente.domain.jornada import HORARIO_INICIAL, Horario, validar_cambio_horario
from agente.infrastructure.db.jornada import AlertaTecnica


class RepositorioHorarioMemoria:
    def __init__(self, inicial: Horario = HORARIO_INICIAL) -> None:
        self._horarios = [inicial]

    def vigente(self) -> Horario:
        return self._horarios[-1]

    def cambiar(
        self, apertura: time, cierre: time, *, usuario_id: int | None, ahora: datetime | None = None
    ) -> Horario:
        ahora = ahora or datetime.now(UTC)
        validar_cambio_horario(self.vigente(), apertura, cierre, ahora)
        self._horarios.append(Horario(apertura, cierre, usuario_id, ahora))
        return self.vigente()

    def historial(self) -> list[Horario]:
        return list(reversed(self._horarios))


class RepositorioAlertasMemoria:
    def __init__(self) -> None:
        self._alertas: list[AlertaTecnica] = []
        self._ids = count(1)

    def registrar(
        self, tipo: TipoAlertaTecnica, fecha_jornada: date | None, mensaje: str, datos: dict[str, Any] | None = None
    ) -> bool:
        if fecha_jornada is not None and self.existe(tipo, fecha_jornada):
            return False
        self._alertas.append(AlertaTecnica(next(self._ids), tipo, fecha_jornada, mensaje, datos, datetime.now(UTC)))
        return True

    def existe(self, tipo: TipoAlertaTecnica, fecha_jornada: date) -> bool:
        return any(a.tipo is tipo and a.fecha_jornada == fecha_jornada for a in self._alertas)

    def listar(self) -> list[AlertaTecnica]:
        return list(reversed(self._alertas))
