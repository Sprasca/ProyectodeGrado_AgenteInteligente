"""Cierre diario programado (RF06 paso 1: "de forma automática al llegar la hora configurada de cierre").

Lo ejecuta el mismo proceso que vigila DIRECTORIO_ENTRADA (scripts/recibir_facturas.py), que llama a
revisar() en cada vuelta. Cuando pasa la HORA_CIERRE de una jornada (leída de la base en cada revisión),
hace UN SOLO intento de generar su reporte de CIERRE, sin usuario y con corte a esa hora. Si el proceso no
estaba corriendo a esa hora, hace ese mismo intento al arrancar, con el corte original.

Sin reintentos: si la jornada queda sin reporte de cierre (alertas sin resolver, descuadre, error), se
registra la notificación técnica JORNADA_SIN_CIERRE para el ADMIN (siempre que la jornada haya recibido
alguna transacción sin contar las RECHAZADAS; si todas están en alerta, también), con el número de alertas
pendientes, y queda un ERROR en el log. Esa notificación es además la constancia de que el intento ya se
hizo: al reiniciar el proceso no se repite. Después, el auxiliar o el contador generan el cierre a mano
(POST /reportes/consolidar con tipo CIERRE) y la notificación queda atendida sola. Si la base no responde,
no se puede registrar nada: queda solo el ERROR en el log.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime

from ..domain.enums import TipoNotificacion, TipoReporte
from ..domain.errors import ConsolidacionBloqueadaError, JornadaCerradaError, SinTransaccionesError
from ..domain.jornada import Horario, corte_de_la_jornada, en_colombia, ultima_jornada_terminada
from ..domain.notificaciones import causa_jornada_sin_cierre, clave_jornada
from ..infrastructure.db.jornada import RepositorioHorario
from ..infrastructure.db.notificaciones import RepositorioNotificaciones
from ..infrastructure.db.reportes import RepositorioReportes
from ..infrastructure.db.transacciones import RepositorioNoDisponibleError
from .reportes import consolidar_reporte

log = logging.getLogger("agente.reportes")

SIN_CIERRE = "la jornada no tiene reporte de cierre"


class CierreProgramado:
    def __init__(
        self,
        reportes: RepositorioReportes,
        horarios: RepositorioHorario,
        notificaciones: RepositorioNotificaciones,
        ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.reportes = reportes
        self.horarios = horarios
        self.notificaciones = notificaciones
        self._ahora = ahora
        self._jornada_revisada: date | None = None

    def revisar(self) -> bool:
        """Si terminó una jornada que este proceso no ha revisado, hace su único intento. True si lo hizo."""
        ahora = self._ahora()
        try:
            horario = self.horarios.vigente()
        except RepositorioNoDisponibleError as exc:
            log.error("Cierre programado: no se pudo leer el horario de la jornada (%s).", exc)
            return False
        jornada = ultima_jornada_terminada(ahora, horario.hora_cierre)
        if self._jornada_revisada == jornada:
            return False
        self._jornada_revisada = jornada  # un solo intento, también si la base no responde
        corte = corte_de_la_jornada(jornada, horario.hora_cierre)
        try:
            self._intentar(jornada, horario, ahora)
        except RepositorioNoDisponibleError as exc:
            log.error(
                "ALERTA TÉCNICA: %s %s y no se pudo intentar el cierre programado: la base no responde (%s). "
                "No se reintenta: genere el cierre a mano cuando la base vuelva.",
                SIN_CIERRE,
                jornada,
                exc,
            )
        except Exception as exc:
            # Descuadre (ConsolidacionInconsistenteError, ya en el log) o un error inesperado.
            log.exception("ALERTA TÉCNICA: falló el cierre programado de la jornada %s.", jornada)
            self._alertar(jornada, f"error al consolidar: {exc}", corte)
        return True

    def _intentar(self, jornada: date, horario: Horario, ahora: datetime) -> None:
        corte = corte_de_la_jornada(jornada, horario.hora_cierre)
        if self.notificaciones.existe(TipoNotificacion.JORNADA_SIN_CIERRE, clave_jornada(jornada)):
            return  # el intento de esta jornada ya se hizo (antes de reiniciar el proceso)
        if self.reportes.resumen_jornada(jornada).cierre_id is not None:
            return  # alguien ya la cerró a mano
        log.info(
            "Cierre programado de la jornada %s, con corte a las %s (hora de Colombia).",
            jornada,
            en_colombia(corte).strftime("%H:%M"),
        )
        try:
            consolidar_reporte(
                self.reportes, horario, usuario_id=None, tipo=TipoReporte.CIERRE, fecha_jornada=jornada, ahora=ahora
            )
        except JornadaCerradaError:
            return
        except SinTransaccionesError:
            # Solo una jornada que no recibió ninguna transacción (sin contar las RECHAZADAS) queda sin cierre
            # y sin alerta. Si recibió alguna, la falta de cierre es trabajo pendiente: se alerta.
            if self.reportes.resumen_jornada(jornada).transacciones_recibidas:
                self._alertar(jornada, "no se pudo generar el reporte de cierre", corte)
        except ConsolidacionBloqueadaError as exc:
            self._alertar(jornada, "transacciones en alerta sin resolver", corte, exc.pendientes)

    def _alertar(self, jornada: date, causa: str, corte: datetime, pendientes: int | None = None) -> None:
        """Notificación técnica JORNADA_SIN_CIERRE para el ADMIN (una por jornada)."""
        try:
            if pendientes is None:
                pendientes = self.reportes.resumen_jornada(jornada).alertas_pendientes
            mensaje = (
                f"ALERTA TÉCNICA: {SIN_CIERRE} {jornada} ({causa}). Transacciones en alerta pendientes: "
                f"{pendientes}. Resuélvalas y genere el cierre a mano."
            )
            datos = {"causa": causa, "alertas_pendientes": pendientes, "corte": corte.isoformat()}
            self.notificaciones.registrar(causa_jornada_sin_cierre(jornada, mensaje, datos))
            log.error(mensaje)
        except Exception:
            log.exception("ALERTA TÉCNICA: %s %s y no se pudo registrar la notificación.", SIN_CIERRE, jornada)


def cierre_por_defecto() -> CierreProgramado:
    """Cierre programado sobre PostgreSQL (DATABASE_URL), con el horario guardado en la base."""
    from ..infrastructure.db.jornada import RepositorioHorarioSQL
    from ..infrastructure.db.notificaciones import RepositorioNotificacionesSQL
    from ..infrastructure.db.reportes import RepositorioReportesSQL
    from ..infrastructure.db.session import fabrica_compartida

    fabrica = fabrica_compartida()
    return CierreProgramado(
        RepositorioReportesSQL(fabrica), RepositorioHorarioSQL(fabrica), RepositorioNotificacionesSQL(fabrica)
    )
