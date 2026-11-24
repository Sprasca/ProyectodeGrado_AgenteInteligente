"""Cierre diario programado (RF06 paso 1: "de forma automática al llegar la hora configurada de cierre").

Lo ejecuta el mismo proceso que vigila DIRECTORIO_ENTRADA (scripts/recibir_facturas.py), que llama a
revisar() en cada vuelta. Hace UN intento por jornada, a la HORA_CIERRE de Colombia, con corte en esa
hora: lo que llegue después queda para el día siguiente. Si el proceso no estaba corriendo a esa hora,
el intento se hace al arrancar (cierre pendiente). Consolida sin usuario (usuario_id None).

Si hay alertas sin resolver o no hay nada que consolidar, no se crea el reporte y queda en el log; el
auxiliar puede consolidar a mano cuando el contador resuelva las alertas. Si la base no responde, se
vuelve a intentar en la siguiente revisión.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time

from ..config import Settings
from ..domain.errors import ConsolidacionBloqueadaError, ConsolidacionInconsistenteError, SinTransaccionesError
from ..domain.jornada import corte_de_la_jornada, en_colombia, fecha_jornada
from ..infrastructure.db.transacciones import RepositorioNoDisponibleError

log = logging.getLogger("agente.reportes")

# Ya quedaron en el log del caso de uso (WARNING, INFO y ERROR): el cierre no los repite.
_RESULTADOS_ESPERADOS = (ConsolidacionBloqueadaError, SinTransaccionesError, ConsolidacionInconsistenteError)


class CierreProgramado:
    def __init__(
        self,
        hora: time,
        consolidar: Callable[[datetime], object],
        ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """consolidar(corte) consolida lo recibido hasta el corte, sin usuario."""
        self.hora = hora
        self._consolidar = consolidar
        self._ahora = ahora
        self._jornada_atendida: date | None = None

    def revisar(self) -> bool:
        """Si ya es la hora de cierre y la jornada no se ha atendido, intenta consolidar. True si lo intentó."""
        ahora = self._ahora()
        jornada = fecha_jornada(ahora)
        corte = corte_de_la_jornada(jornada, self.hora)
        if self._jornada_atendida == jornada or ahora < corte:
            return False
        self._jornada_atendida = jornada
        log.info(
            "Cierre programado de la jornada %s: se consolida lo recibido hasta las %s (hora de Colombia).",
            jornada,
            en_colombia(corte).strftime("%H:%M"),
        )
        try:
            self._consolidar(corte)
        except _RESULTADOS_ESPERADOS:
            pass
        except RepositorioNoDisponibleError as exc:
            self._jornada_atendida = None  # se reintenta en la siguiente revisión
            log.error("Cierre programado de la jornada %s: la base no responde (%s). Se reintentará.", jornada, exc)
        except Exception:
            log.exception("ALERTA TÉCNICA: falló el cierre programado de la jornada %s.", jornada)
        return True


def cierre_por_defecto(settings: Settings) -> CierreProgramado:
    """Consolida en PostgreSQL (DATABASE_URL) a la HORA_CIERRE configurada."""
    from ..infrastructure.db.reportes import RepositorioReportesSQL
    from ..infrastructure.db.session import fabrica_compartida
    from .reportes import consolidar_reporte

    def consolidar(corte: datetime) -> object:
        return consolidar_reporte(RepositorioReportesSQL(fabrica_compartida()), usuario_id=None, corte=corte)

    return CierreProgramado(settings.hora_cierre, consolidar)
