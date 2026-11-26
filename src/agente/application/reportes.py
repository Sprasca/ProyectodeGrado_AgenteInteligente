"""Casos de uso del reporte de transacciones diarias (RF06) y de su exportación (RF07).

Los usan igual la API (reportes parciales y de cierre a mano, del auxiliar o del contador) y el cierre
programado (usuario None). Hasta que exista el módulo de notificaciones (Fase 5), los avisos que pide el
manual quedan en el log: WARNING si hay alertas sin resolver, ERROR ante un error de integridad (alerta
técnica). La jornada sin reporte de cierre es una notificación técnica (ver application.cierre).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

from ..domain.enums import TipoReporte
from ..domain.errors import (
    ConsolidacionBloqueadaError,
    ConsolidacionInconsistenteError,
    ExportacionFallidaError,
    JornadaNoTerminadaError,
    SinTransaccionesError,
    TransicionInvalidaError,
)
from ..domain.jornada import Horario, corte_de_la_jornada, jornada_de, ultima_jornada_terminada
from ..domain.notificaciones import (
    Causa,
    causa_consolidacion_bloqueada,
    causa_descuadre,
    causa_exportacion_fallida,
)
from ..domain.reportes import EstadoJornada, ReporteDiario, estado_de_jornada
from ..infrastructure.db.notificaciones import RepositorioNotificaciones
from ..infrastructure.db.reportes import GeneradorArchivo, RepositorioReportes
from ..infrastructure.erp import xml_reporte

log = logging.getLogger("agente.reportes")


def _notificar(notificaciones: RepositorioNotificaciones | None, causa: Causa) -> None:
    """
    Para las causas que son fallas: la transacción de negocio ya se revirtió, así que se guarda aparte. Si no
    se puede (la base no responde), el error original sigue su curso y queda un ERROR en el log.
    """
    if notificaciones is None:
        return
    try:
        notificaciones.registrar(causa)
    except Exception:
        log.exception("ALERTA TÉCNICA: no se pudo registrar la notificación %s (%s).", causa.tipo, causa.clave)


def jornada_y_corte(
    tipo: TipoReporte, horario: Horario, ahora: datetime, fecha_jornada: date | None = None
) -> tuple[date, datetime]:
    """
    PARCIAL: la jornada en curso, con corte en este momento. Solo se hace en la jornada en curso.
    CIERRE: por defecto la última jornada terminada, con corte a su HORA_CIERRE, aunque se genere más tarde.
    JornadaNoTerminadaError si se pide el cierre antes de esa hora.
    """
    en_curso = jornada_de(ahora, horario.hora_cierre)
    if tipo is TipoReporte.PARCIAL:
        if fecha_jornada is not None and fecha_jornada != en_curso:
            raise TransicionInvalidaError(
                f"Un reporte parcial solo se hace en la jornada en curso ({en_curso}); para la jornada "
                f"{fecha_jornada} solo queda el reporte de cierre."
            )
        return en_curso, ahora
    fecha = fecha_jornada or ultima_jornada_terminada(ahora, horario.hora_cierre)
    corte = corte_de_la_jornada(fecha, horario.hora_cierre)
    if ahora < corte:
        raise JornadaNoTerminadaError(
            f"La jornada {fecha} aún no termina: su reporte de cierre se hace desde las {horario.hora_cierre:%H:%M} "
            "(hora de Colombia). Mientras tanto, puede consolidar reportes parciales."
        )
    return fecha, corte


def consolidar_reporte(
    repositorio: RepositorioReportes,
    horario: Horario,
    *,
    usuario_id: int | None,
    tipo: TipoReporte = TipoReporte.PARCIAL,
    fecha_jornada: date | None = None,
    ahora: datetime | None = None,
    notificaciones: RepositorioNotificaciones | None = None,
) -> ReporteDiario:
    """
    Consolida un reporte PARCIAL o de CIERRE (ver jornada_y_corte). Relanza los errores tras dejarlos en el
    log y, con `notificaciones`, notificar: el bloqueo por alertas al contador (RF06 1.a) y el descuadre al
    ADMIN (RF06 3.a). El reporte disponible (RF06 paso 8) lo notifica el repositorio en su transacción.
    """
    ahora = ahora or datetime.now(UTC)
    jornada, corte = jornada_y_corte(tipo, horario, ahora, fecha_jornada)
    quien = f"usuario {usuario_id}" if usuario_id is not None else "cierre programado"
    try:
        reporte = repositorio.consolidar(fecha_jornada=jornada, tipo=tipo, corte=corte, usuario_id=usuario_id)
    except ConsolidacionBloqueadaError as exc:
        # RF06 1.a: el manual pide notificar al contador; hasta la Fase 5, un WARNING con el número pendiente.
        log.warning("Reporte %s de la jornada %s suspendido (%s): %s", tipo, jornada, quien, exc)
        _notificar(notificaciones, causa_consolidacion_bloqueada(jornada, exc.pendientes, tipo.value))
        raise
    except SinTransaccionesError as exc:
        log.info("Reporte %s de la jornada %s (%s): %s", tipo, jornada, quien, exc)
        raise
    except ConsolidacionInconsistenteError as exc:
        # RF06 3.a: error de integridad del sistema → alerta técnica al responsable de mantenimiento.
        log.error("ALERTA TÉCNICA: reporte %s de la jornada %s abortado (%s): %s", tipo, jornada, quien, exc)
        _notificar(notificaciones, causa_descuadre(jornada, str(exc)))
        raise
    log.info(
        "Reporte %s n.º %d de la jornada %s consolidado (%s): %d transacciones, débitos = créditos = %s.",
        reporte.tipo,
        reporte.numero,
        jornada,
        quien,
        reporte.numero_transacciones,
        reporte.total_debito,
    )
    return reporte


def estado_jornada(
    repositorio: RepositorioReportes,
    horario: Horario,
    *,
    fecha_jornada: date | None = None,
    ahora: datetime | None = None,
) -> EstadoJornada:
    """Lo que lleva la jornada (por defecto, la en curso) y si se puede consolidar un parcial o el cierre."""
    ahora = ahora or datetime.now(UTC)
    fecha = fecha_jornada or jornada_de(ahora, horario.hora_cierre)
    return estado_de_jornada(repositorio.resumen_jornada(fecha), horario, ahora)


def exportar_reporte(
    repositorio: RepositorioReportes,
    id_reporte: uuid.UUID,
    *,
    usuario_id: int | None,
    generar: GeneradorArchivo | None = None,
    notificaciones: RepositorioNotificaciones | None = None,
) -> ReporteDiario:
    """
    RF07: el contador autoriza y en el mismo paso se genera el XML, se valida contra el esquema XSD y el
    reporte pasa a EXPORTADO, todo en una sola transacción de base de datos. Si la generación o la
    validación fallan, el reporte sigue CONSOLIDADO y queda un ERROR en el log (anomalía 4).
    Sin `generar`, el XML versionado de agente.infrastructure.erp.xml_reporte (validado).
    """
    try:
        reporte = repositorio.exportar(
            id_reporte, usuario_id=usuario_id, generar=generar or xml_reporte.generar_xml_reporte
        )
    except ExportacionFallidaError as exc:
        log.error("ALERTA TÉCNICA: exportación del reporte %s fallida (usuario %s): %s", id_reporte, usuario_id, exc)
        _notificar(notificaciones, causa_exportacion_fallida(id_reporte, str(exc)))
        raise
    log.info(
        "Reporte %s exportado por el usuario %s (SHA-256 %s).", reporte.id, usuario_id, reporte.archivo_sha256
    )
    return reporte
