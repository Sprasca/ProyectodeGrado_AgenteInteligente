"""Casos de uso del reporte de transacciones diarias (RF06) y de su exportación (RF07).

Los usan igual la API (consolidación manual del auxiliar o del contador) y el cierre programado
(usuario None). Hasta que exista el módulo de notificaciones (Fase 5), los avisos que pide el manual
quedan en el log: WARNING si hay alertas sin resolver, ERROR ante un error de integridad (alerta técnica).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..domain.errors import ConsolidacionBloqueadaError, ConsolidacionInconsistenteError, SinTransaccionesError
from ..domain.jornada import fecha_jornada
from ..domain.reportes import ReporteDiario
from ..infrastructure.db.reportes import RepositorioReportes

log = logging.getLogger("agente.reportes")


def consolidar_reporte(
    repositorio: RepositorioReportes,
    *,
    usuario_id: int | None,
    corte: datetime | None = None,
) -> ReporteDiario:
    """
    Consolida lo recibido hasta `corte` (por defecto, ahora). La fecha de la jornada es la del corte
    en hora de Colombia. Relanza los errores del repositorio después de dejarlos en el log.
    """
    corte = corte or datetime.now(UTC)
    jornada = fecha_jornada(corte)
    quien = f"usuario {usuario_id}" if usuario_id is not None else "cierre programado"
    try:
        reporte = repositorio.consolidar(corte=corte, fecha_jornada=jornada, usuario_id=usuario_id)
    except ConsolidacionBloqueadaError as exc:
        # RF06 1.a: el manual pide notificar al contador; hasta la Fase 5, un WARNING con el número pendiente.
        log.warning("Consolidación de la jornada %s suspendida (%s): %s", jornada, quien, exc)
        raise
    except SinTransaccionesError:
        log.info("Jornada %s (%s): no hay transacciones aprobadas sin reporte; no se crea un reporte.", jornada, quien)
        raise
    except ConsolidacionInconsistenteError as exc:
        # RF06 3.a: error de integridad del sistema → alerta técnica al responsable de mantenimiento.
        log.error("ALERTA TÉCNICA: consolidación de la jornada %s abortada (%s): %s", jornada, quien, exc)
        raise
    log.info(
        "Reporte %s de la jornada %s consolidado (%s): %d transacciones, débitos = créditos = %s.",
        reporte.id,
        jornada,
        quien,
        reporte.numero_transacciones,
        reporte.total_debito,
    )
    return reporte
