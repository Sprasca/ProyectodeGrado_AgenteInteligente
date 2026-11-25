"""Horario de la jornada en memoria. SOLO para pruebas; misma interfaz que el de SQL."""

from datetime import UTC, datetime, time

from agente.domain.jornada import HORARIO_INICIAL, Horario, validar_cambio_horario


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
