"""Envío por correo de las notificaciones (RF04), desde el proceso del vigilante.

La notificación ya está guardada en la bandeja (en la misma transacción que su causa); el correo es un canal
OPCIONAL que se envía después. Cada envío lleva su estado (PENDIENTE, ENVIADA, FALLIDA), sus intentos y su
último error. Hasta CORREO_INTENTOS intentos con espera creciente; si fallan todos, queda FALLIDA, la
notificación sigue en la bandeja del destinatario y se avisa al ADMIN (CORREO_NO_ENVIADO, solo en la bandeja).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..config import Settings
from ..domain.notificaciones import causa_correo_no_enviado, espera_del_reintento
from ..infrastructure.db.notificaciones import RepositorioNotificaciones
from ..infrastructure.db.transacciones import RepositorioNoDisponibleError

log = logging.getLogger("agente.notificaciones")

# enviar(destinatario, asunto, cuerpo); lanza una excepción si falla.
Enviador = Callable[[str, str, str], None]


class DespachadorCorreos:
    def __init__(
        self,
        notificaciones: RepositorioNotificaciones,
        enviar: Enviador,
        *,
        intentos_maximos: int = 3,
        espera_inicial: float = 60.0,
        ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.notificaciones = notificaciones
        self._enviar = enviar
        self.intentos_maximos = intentos_maximos
        self.espera_inicial = espera_inicial
        self._ahora = ahora

    def revisar(self) -> int:
        """Envía los pendientes cuyo turno ya llegó. Devuelve cuántos se enviaron."""
        ahora = self._ahora()
        try:
            pendientes = self.notificaciones.envios_pendientes(ahora)
        except RepositorioNoDisponibleError as exc:
            log.error("No se pudieron consultar los correos pendientes: %s", exc)
            return 0
        enviados = 0
        for envio in pendientes:
            try:
                self._enviar(envio.destinatario, envio.asunto, envio.cuerpo)
            except Exception as exc:
                self._fallo(envio, str(exc) or type(exc).__name__, ahora)
                continue
            self.notificaciones.marcar_enviado(envio.id, ahora)
            enviados += 1
        return enviados

    def _fallo(self, envio, error: str, ahora: datetime) -> None:
        intentos = envio.intentos + 1
        if intentos < self.intentos_maximos:
            espera = espera_del_reintento(intentos, self.espera_inicial)
            self.notificaciones.marcar_fallo(envio.id, error, ahora + timedelta(seconds=espera))
            log.warning(
                "Correo a %s no enviado (intento %d de %d): %s. Se reintenta en %g s.",
                envio.destinatario,
                intentos,
                self.intentos_maximos,
                error,
                espera,
            )
            return
        self.notificaciones.marcar_fallo(envio.id, error, None)
        log.error("Correo a %s FALLIDO tras %d intentos: %s", envio.destinatario, intentos, error)
        self.notificaciones.registrar(causa_correo_no_enviado(envio, intentos, error))


def despachador_por_defecto(settings: Settings) -> DespachadorCorreos:
    """Sobre PostgreSQL (DATABASE_URL) y el SMTP de .env. Solo tiene sentido con settings.correo_activo."""
    from ..infrastructure.db.notificaciones import RepositorioNotificacionesSQL
    from ..infrastructure.db.session import fabrica_compartida
    from ..infrastructure.notificaciones.correo import EnviadorSMTP

    return DespachadorCorreos(
        RepositorioNotificacionesSQL(fabrica_compartida()),
        EnviadorSMTP(settings),
        intentos_maximos=settings.correo_intentos,
        espera_inicial=settings.correo_espera_inicial,
    )
