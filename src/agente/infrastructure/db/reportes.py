"""Persistencia de los reportes de transacciones diarias (RF06, RF07)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ...domain.enums import EstadoReporte, EstadoTransaccion, TipoEvento, TipoNotificacion, TipoReporte
from ...domain.errors import (
    ExportacionFallidaError,
    JornadaCerradaError,
    LimiteDeReportesError,
    ReporteNoEncontradoError,
)
from ...domain.notificaciones import clave_jornada, resolucion_cierre
from ...domain.reportes import (
    CUPO_CIERRE,
    ESTADOS_CONSOLIDABLES,
    ArchivoExportado,
    Consolidado,
    EventoReporte,
    ReporteDiario,
    ResumenJornada,
    consolidar_movimientos,
    exigir_sin_alertas,
    exigir_transacciones,
    movimientos_a_json,
    movimientos_desde_json,
    nombre_archivo_reporte,
    subtotales_a_json,
    subtotales_desde_json,
    validar_anulacion,
    validar_exportacion,
    validar_nuevo_reporte,
)
from ...domain.transacciones import exigir_motivo
from .models import EventoReporteModel, ReporteDiarioModel, ReporteTransaccionModel, TransaccionModel
from .notificaciones import atender_en_sesion
from .transacciones import TransaccionRegistrada, a_registro, nuevo_evento, transaccion_bd

# generar(reporte, transacciones, id del autor, fecha de autorización) -> contenido del archivo
# Espacio del bloqueo consultivo de PostgreSQL que serializa las consolidaciones de una misma jornada.
_BLOQUEO_JORNADA = 6_0001

GeneradorArchivo = Callable[[ReporteDiario, Sequence[TransaccionRegistrada], int | None, datetime], bytes]


class RepositorioReportes(Protocol):
    def consolidar(
        self,
        *,
        fecha_jornada: date,
        tipo: TipoReporte,
        corte: datetime,
        usuario_id: int | None = None,
    ) -> ReporteDiario:
        """
        RF06: toma las transacciones aprobadas de la jornada que aún no tienen reporte y las consolida en un
        reporte CONSOLIDADO del tipo pedido, con el siguiente número de la jornada (evento CONSOLIDADO;
        CONSOLIDADA en cada transacción). `corte` queda registrado: el momento de consolidar en un PARCIAL,
        la HORA_CIERRE de la jornada en el CIERRE (lo decide el caso de uso).
        - JornadaCerradaError si la jornada ya tiene un cierre vigente.
        - LimiteDeReportesError si es un PARCIAL y ya hay 4 vigentes.
        - ConsolidacionBloqueadaError si hay transacciones EN_ALERTA en la jornada.
        - SinTransaccionesError si un PARCIAL quedaría vacío, o si la jornada no tiene nada que cerrar.
        - ConsolidacionInconsistenteError si débitos y créditos no cuadran (no se guarda nada).
        usuario_id es None cuando consolida el cierre programado.
        """
        ...

    def resumen_jornada(self, fecha_jornada: date) -> ResumenJornada:
        """Reportes vigentes, alertas pendientes y transacciones aprobadas sin reporte de la jornada."""
        ...

    def obtener(self, id_reporte: uuid.UUID) -> ReporteDiario | None: ...

    def listar(self, estado: EstadoReporte | None = None, fecha_jornada: date | None = None) -> list[ReporteDiario]:
        """Del más reciente al más antiguo."""
        ...

    def eventos(self, id_reporte: uuid.UUID) -> list[EventoReporte]:
        """Cambios de estado, del más antiguo al más reciente. ReporteNoEncontradoError si no existe."""
        ...

    def exportar(self, id_reporte: uuid.UUID, *, usuario_id: int | None, generar: GeneradorArchivo) -> ReporteDiario:
        """
        RF07: el contador autoriza y, en el mismo paso y en la misma transacción de base de datos, se genera el
        archivo, se guarda en la base y el reporte pasa a EXPORTADO, con el autor, la fecha y hora y el SHA-256
        del archivo (evento EXPORTADO; EXPORTADA en cada transacción, con el usuario que exportó).
        - TransicionInvalidaError si no está CONSOLIDADO (un EXPORTADO no se exporta dos veces) o si
          alguna de sus transacciones no está aprobada.
        - ExportacionFallidaError si `generar` falla (también si el XML no cumple el esquema XSD): el reporte
          sigue CONSOLIDADO, sin archivo (anomalía 4).
        """
        ...

    def archivo(self, id_reporte: uuid.UUID) -> ArchivoExportado | None:
        """El XML exportado, o None si el reporte no se ha exportado. ReporteNoEncontradoError si no existe."""
        ...

    def anular(self, id_reporte: uuid.UUID, *, motivo: str, usuario_id: int | None) -> ReporteDiario:
        """
        Ampliación del manual: CONSOLIDADO → ANULADO, con motivo obligatorio (MotivoInvalidoError) y evento
        ANULADO. Sus transacciones quedan libres (reporte_id NULL, evento LIBERADA) para corregirse y
        consolidarse de nuevo; el reporte conserva su lista de transacciones y sus totales.
        TransicionInvalidaError si no está CONSOLIDADO (un EXPORTADO nunca se anula) o si es un PARCIAL de una
        jornada con cierre vigente. Anular el CIERRE reabre la jornada.
        """
        ...


def datos_consolidado(consolidado: Consolidado) -> dict[str, Any]:
    return {
        "numero_transacciones": consolidado.numero_transacciones,
        "total_debito": str(consolidado.total_debito),
        "total_credito": str(consolidado.total_credito),
        "valor_total": str(consolidado.valor_total),
    }


def _a_reporte(fila: ReporteDiarioModel, transacciones: list[uuid.UUID]) -> ReporteDiario:
    return ReporteDiario(
        id=fila.id,
        fecha_jornada=fila.fecha_jornada,
        corte=fila.corte,
        estado=EstadoReporte(fila.estado),
        tipo=TipoReporte(fila.tipo),
        numero=fila.numero,
        numero_transacciones=fila.numero_transacciones,
        total_debito=fila.total_debito,
        total_credito=fila.total_credito,
        valor_total=fila.valor_total,
        movimientos=movimientos_desde_json(fila.movimientos),
        subtotales=subtotales_desde_json(fila.subtotales),
        transacciones=tuple(transacciones),
        creado_en=fila.creado_en,
        exportado_en=fila.exportado_en,
        exportado_por=fila.exportado_por,
        archivo_sha256=fila.archivo_sha256,
    )


def datos_vinculo(reporte_id: uuid.UUID, fecha_jornada: date, tipo: TipoReporte, numero: int) -> dict[str, Any]:
    """Lo que queda en los eventos de cada transacción sobre su reporte."""
    return {
        "reporte_id": str(reporte_id),
        "fecha_jornada": fecha_jornada.isoformat(),
        "tipo": tipo.value,
        "numero": numero,
    }


def datos_exportacion(archivo: ArchivoExportado) -> dict[str, Any]:
    return {"archivo": archivo.nombre, "bytes": len(archivo.contenido), "sha256": archivo.sha256}


def generar_archivo(
    generar: GeneradorArchivo,
    reporte: ReporteDiario,
    transacciones: Sequence[TransaccionRegistrada],
    usuario_id: int | None,
    fecha: datetime,
) -> ArchivoExportado:
    """Llama al generador; cualquier falla se convierte en ExportacionFallidaError (RF07 anomalía 4)."""
    try:
        contenido = generar(reporte, transacciones, usuario_id, fecha)
    except Exception as exc:
        raise ExportacionFallidaError(
            f"No se pudo generar el archivo del reporte {reporte.id}: {exc}. El reporte sigue CONSOLIDADO."
        ) from exc
    return ArchivoExportado(nombre_archivo_reporte(reporte), contenido, hashlib.sha256(contenido).hexdigest())


def _a_evento(fila: EventoReporteModel) -> EventoReporte:
    return EventoReporte(
        id=fila.id,
        reporte_id=fila.reporte_id,
        tipo=EstadoReporte(fila.tipo),
        usuario_id=fila.usuario_id,
        fecha=fila.fecha,
        motivo=fila.motivo,
        despues=fila.despues,
    )


def _evento_reporte(
    reporte_id: uuid.UUID,
    tipo: EstadoReporte,
    usuario_id: int | None,
    *,
    motivo: str | None = None,
    despues: dict[str, Any] | None = None,
) -> EventoReporteModel:
    return EventoReporteModel(
        reporte_id=reporte_id,
        tipo=tipo.value,
        usuario_id=usuario_id,
        fecha=datetime.now(UTC),
        motivo=motivo,
        despues=despues,
    )


class RepositorioReportesSQL:
    def __init__(self, fabrica_sesiones: sessionmaker[Session]) -> None:
        self._fabrica = fabrica_sesiones

    def _transacciones_de(self, sesion: Session, id_reporte: uuid.UUID) -> list[uuid.UUID]:
        return list(
            sesion.scalars(
                select(ReporteTransaccionModel.transaccion_id)
                .join(TransaccionModel, TransaccionModel.id == ReporteTransaccionModel.transaccion_id)
                .where(ReporteTransaccionModel.reporte_id == id_reporte)
                .order_by(TransaccionModel.creado_en, TransaccionModel.numero_factura)
            )
        )

    def _resumen(self, sesion: Session, fecha_jornada: date) -> ResumenJornada:
        vigentes = sesion.execute(
            select(ReporteDiarioModel.cupo, ReporteDiarioModel.id).where(
                ReporteDiarioModel.fecha_jornada == fecha_jornada,
                ReporteDiarioModel.estado != EstadoReporte.ANULADO.value,
            )
        ).all()
        ultimo = sesion.scalar(
            select(func.coalesce(func.max(ReporteDiarioModel.numero), 0)).where(
                ReporteDiarioModel.fecha_jornada == fecha_jornada
            )
        )
        de_la_jornada = select(func.count()).select_from(TransaccionModel).where(
            TransaccionModel.fecha_jornada == fecha_jornada
        )
        alertas = sesion.scalar(de_la_jornada.where(TransaccionModel.estado == EstadoTransaccion.EN_ALERTA.value))
        sin_reporte = sesion.scalar(
            de_la_jornada.where(
                TransaccionModel.estado.in_([e.value for e in ESTADOS_CONSOLIDABLES]),
                TransaccionModel.reporte_id.is_(None),
            )
        )
        return ResumenJornada(
            fecha_jornada=fecha_jornada,
            cupos_ocupados=frozenset(cupo for cupo, _ in vigentes),
            cierre_id=next((id_ for cupo, id_ in vigentes if cupo == CUPO_CIERRE), None),
            ultimo_numero=ultimo,
            alertas_pendientes=alertas,
            transacciones_sin_reporte=sin_reporte,
            transacciones_recibidas=sesion.scalar(
                de_la_jornada.where(TransaccionModel.estado != EstadoTransaccion.RECHAZADA.value)
            ),
        )

    def resumen_jornada(self, fecha_jornada: date) -> ResumenJornada:
        with transaccion_bd(self._fabrica) as sesion:
            return self._resumen(sesion, fecha_jornada)

    def consolidar(
        self,
        *,
        fecha_jornada: date,
        tipo: TipoReporte,
        corte: datetime,
        usuario_id: int | None = None,
    ) -> ReporteDiario:
        try:
            with transaccion_bd(self._fabrica) as sesion:
                # Una consolidación a la vez por jornada: el número y el cupo se calculan sin carreras. Las
                # restricciones de la base (cupo único entre vigentes, un cierre vigente) son la última defensa.
                sesion.execute(
                    text("SELECT pg_advisory_xact_lock(:espacio, :dia)"),
                    {"espacio": _BLOQUEO_JORNADA, "dia": fecha_jornada.toordinal()},
                )
                resumen = self._resumen(sesion, fecha_jornada)
                cupo = validar_nuevo_reporte(resumen, tipo)
                # FOR UPDATE: nadie corrige ni rechaza estas transacciones mientras se consolidan.
                filas = list(
                    sesion.scalars(
                        select(TransaccionModel)
                        .options(selectinload(TransaccionModel.partidas))
                        .where(
                            TransaccionModel.fecha_jornada == fecha_jornada,
                            TransaccionModel.estado.in_([e.value for e in ESTADOS_CONSOLIDABLES]),
                            TransaccionModel.reporte_id.is_(None),
                        )
                        .order_by(TransaccionModel.creado_en, TransaccionModel.numero_factura)
                        .with_for_update()
                    )
                )
                exigir_transacciones(tipo, len(filas), resumen.parciales_vigentes)
                consolidado = consolidar_movimientos([a_registro(t) for t in filas])
                reporte = ReporteDiarioModel(
                    id=uuid.uuid4(),
                    fecha_jornada=fecha_jornada,
                    tipo=tipo.value,
                    numero=resumen.ultimo_numero + 1,
                    cupo=cupo,
                    corte=corte,
                    estado=EstadoReporte.CONSOLIDADO.value,
                    numero_transacciones=consolidado.numero_transacciones,
                    total_debito=consolidado.total_debito,
                    total_credito=consolidado.total_credito,
                    valor_total=consolidado.valor_total,
                    movimientos=movimientos_a_json(consolidado.movimientos),
                    subtotales=subtotales_a_json(consolidado.subtotales),
                    creado_en=datetime.now(UTC),
                )
                sesion.add(reporte)
                sesion.flush()
                vinculo = datos_vinculo(reporte.id, fecha_jornada, tipo, reporte.numero)
                for t in filas:
                    t.reporte_id = reporte.id
                    sesion.add(ReporteTransaccionModel(reporte_id=reporte.id, transaccion_id=t.id))
                    sesion.add(nuevo_evento(t.id, TipoEvento.CONSOLIDADA, usuario_id, despues=vinculo))
                sesion.add(
                    _evento_reporte(
                        reporte.id, EstadoReporte.CONSOLIDADO, usuario_id, despues=datos_consolidado(consolidado)
                    )
                )
                if tipo is TipoReporte.CIERRE:
                    atender_en_sesion(
                        sesion,
                        TipoNotificacion.JORNADA_SIN_CIERRE,
                        clave_jornada(fecha_jornada),
                        resolucion_cierre(reporte.id),
                    )
                sesion.flush()
                return _a_reporte(reporte, [t.id for t in filas])
        except IntegrityError as exc:
            restriccion = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if restriccion == "uq_reporte_diario_cierre_vigente" or (
                restriccion == "uq_reporte_diario_cupo_vigente" and tipo is TipoReporte.CIERRE
            ):
                raise JornadaCerradaError(f"La jornada {fecha_jornada} ya tiene un reporte de cierre vigente.") from exc
            if restriccion == "uq_reporte_diario_cupo_vigente":
                raise LimiteDeReportesError(
                    f"La jornada {fecha_jornada} ya tiene 4 reportes parciales vigentes."
                ) from exc
            raise

    def obtener(self, id_reporte: uuid.UUID) -> ReporteDiario | None:
        with transaccion_bd(self._fabrica) as sesion:
            fila = sesion.get(ReporteDiarioModel, id_reporte)
            return _a_reporte(fila, self._transacciones_de(sesion, fila.id)) if fila is not None else None

    def listar(self, estado: EstadoReporte | None = None, fecha_jornada: date | None = None) -> list[ReporteDiario]:
        consulta = select(ReporteDiarioModel).order_by(ReporteDiarioModel.creado_en.desc(), ReporteDiarioModel.id)
        if estado is not None:
            consulta = consulta.where(ReporteDiarioModel.estado == estado.value)
        if fecha_jornada is not None:
            consulta = consulta.where(ReporteDiarioModel.fecha_jornada == fecha_jornada)
        with transaccion_bd(self._fabrica) as sesion:
            return [_a_reporte(f, self._transacciones_de(sesion, f.id)) for f in sesion.scalars(consulta)]

    def eventos(self, id_reporte: uuid.UUID) -> list[EventoReporte]:
        with transaccion_bd(self._fabrica) as sesion:
            if sesion.get(ReporteDiarioModel, id_reporte) is None:
                raise ReporteNoEncontradoError(f"No existe el reporte {id_reporte}.")
            filas = sesion.scalars(
                select(EventoReporteModel)
                .where(EventoReporteModel.reporte_id == id_reporte)
                .order_by(EventoReporteModel.fecha, EventoReporteModel.id)
            )
            return [_a_evento(f) for f in filas]

    def exportar(self, id_reporte: uuid.UUID, *, usuario_id: int | None, generar: GeneradorArchivo) -> ReporteDiario:
        with transaccion_bd(self._fabrica) as sesion:
            # FOR UPDATE: dos exportaciones simultáneas del mismo reporte no generan dos archivos.
            fila = sesion.get(ReporteDiarioModel, id_reporte, with_for_update=True)
            if fila is None:
                raise ReporteNoEncontradoError(f"No existe el reporte {id_reporte}.")
            validar_exportacion(EstadoReporte(fila.estado))
            incluidas = list(
                sesion.scalars(
                    select(TransaccionModel)
                    .options(selectinload(TransaccionModel.partidas))
                    .where(TransaccionModel.reporte_id == id_reporte)
                    .order_by(TransaccionModel.creado_en, TransaccionModel.numero_factura)
                )
            )
            exigir_sin_alertas(EstadoTransaccion(t.estado) for t in incluidas)
            reporte = _a_reporte(fila, [t.id for t in incluidas])
            ahora = datetime.now(UTC)
            archivo = generar_archivo(generar, reporte, [a_registro(t) for t in incluidas], usuario_id, ahora)
            fila.estado = EstadoReporte.EXPORTADO.value
            fila.archivo_xml = archivo.contenido
            fila.archivo_sha256 = archivo.sha256
            fila.exportado_en = ahora
            fila.exportado_por = usuario_id
            exportado = datos_exportacion(archivo)
            sesion.add(_evento_reporte(fila.id, EstadoReporte.EXPORTADO, usuario_id, despues=exportado))
            vinculo = datos_vinculo(fila.id, fila.fecha_jornada, TipoReporte(fila.tipo), fila.numero)
            for t in incluidas:
                sesion.add(nuevo_evento(t.id, TipoEvento.EXPORTADA, usuario_id, despues=vinculo | exportado))
            sesion.flush()
            return _a_reporte(fila, reporte.transacciones)

    def archivo(self, id_reporte: uuid.UUID) -> ArchivoExportado | None:
        with transaccion_bd(self._fabrica) as sesion:
            fila = sesion.get(ReporteDiarioModel, id_reporte)
            if fila is None:
                raise ReporteNoEncontradoError(f"No existe el reporte {id_reporte}.")
            if fila.archivo_xml is None:
                return None
            reporte = _a_reporte(fila, [])
            return ArchivoExportado(nombre_archivo_reporte(reporte), fila.archivo_xml, fila.archivo_sha256)

    def anular(self, id_reporte: uuid.UUID, *, motivo: str, usuario_id: int | None) -> ReporteDiario:
        motivo = exigir_motivo(motivo, "anular un reporte")
        with transaccion_bd(self._fabrica) as sesion:
            fila = sesion.get(ReporteDiarioModel, id_reporte, with_for_update=True)
            if fila is None:
                raise ReporteNoEncontradoError(f"No existe el reporte {id_reporte}.")
            cierre = self._resumen(sesion, fila.fecha_jornada).cierre_id
            validar_anulacion(EstadoReporte(fila.estado), TipoReporte(fila.tipo), cierre is not None)
            vigentes = sesion.scalars(
                select(TransaccionModel).where(TransaccionModel.reporte_id == id_reporte).with_for_update()
            )
            liberada = {"reporte_id": str(id_reporte), "reporte_estado": EstadoReporte.ANULADO.value}
            for t in vigentes:
                t.reporte_id = None
                sesion.add(nuevo_evento(t.id, TipoEvento.LIBERADA, usuario_id, motivo=motivo, despues=liberada))
            fila.estado = EstadoReporte.ANULADO.value
            sesion.add(_evento_reporte(fila.id, EstadoReporte.ANULADO, usuario_id, motivo=motivo))
            sesion.flush()
            return _a_reporte(fila, self._transacciones_de(sesion, fila.id))
