"""Repositorio de reportes en memoria. SOLO para pruebas; misma interfaz que RepositorioReportesSQL.

Comparte el estado con un RepositorioMemoria de transacciones, como en SQL comparten la base.
"""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from itertools import count

from repositorio_memoria import RepositorioMemoria

from agente.domain.enums import EstadoReporte, EstadoTransaccion, TipoEvento, TipoReporte
from agente.domain.errors import ReporteNoEncontradoError
from agente.domain.reportes import (
    CUPO_CIERRE,
    ESTADOS_CONSOLIDABLES,
    ArchivoExportado,
    EventoReporte,
    ReporteDiario,
    ResumenJornada,
    consolidar_movimientos,
    exigir_sin_alertas,
    exigir_transacciones,
    validar_anulacion,
    validar_exportacion,
    validar_nuevo_reporte,
)
from agente.domain.transacciones import exigir_motivo
from agente.infrastructure.db.reportes import (
    GeneradorArchivo,
    datos_consolidado,
    datos_exportacion,
    datos_vinculo,
    generar_archivo,
)


class RepositorioReportesMemoria:
    def __init__(self, transacciones: RepositorioMemoria) -> None:
        self.transacciones = transacciones
        self._reportes: dict[uuid.UUID, ReporteDiario] = {}
        self._cupos: dict[uuid.UUID, int] = {}
        self._eventos: list[EventoReporte] = []
        self._archivos: dict[uuid.UUID, ArchivoExportado] = {}
        self._ids_evento = count(1)

    def _registrar(self, reporte_id, tipo, usuario_id, *, motivo=None, despues=None) -> None:
        self._eventos.append(
            EventoReporte(next(self._ids_evento), reporte_id, tipo, usuario_id, datetime.now(UTC), motivo, despues)
        )

    def _de_la_jornada(self, fecha_jornada: date):
        todas = sorted(self.transacciones._por_id.values(), key=lambda t: (t.creado_en, t.numero_factura))
        return [t for t in todas if t.fecha_jornada == fecha_jornada]

    def resumen_jornada(self, fecha_jornada: date) -> ResumenJornada:
        de_la_jornada = [r for r in self._reportes.values() if r.fecha_jornada == fecha_jornada]
        vigentes = [r for r in de_la_jornada if r.estado is not EstadoReporte.ANULADO]
        transacciones = self._de_la_jornada(fecha_jornada)
        return ResumenJornada(
            fecha_jornada=fecha_jornada,
            cupos_ocupados=frozenset(self._cupos[r.id] for r in vigentes),
            cierre_id=next((r.id for r in vigentes if self._cupos[r.id] == CUPO_CIERRE), None),
            ultimo_numero=max((r.numero for r in de_la_jornada), default=0),
            alertas_pendientes=sum(1 for t in transacciones if t.estado is EstadoTransaccion.EN_ALERTA),
            transacciones_sin_reporte=sum(
                1 for t in transacciones if t.estado in ESTADOS_CONSOLIDABLES and t.reporte_id is None
            ),
        )

    def consolidar(
        self,
        *,
        fecha_jornada: date,
        tipo: TipoReporte,
        corte: datetime,
        usuario_id: int | None = None,
    ) -> ReporteDiario:
        resumen = self.resumen_jornada(fecha_jornada)
        cupo = validar_nuevo_reporte(resumen, tipo)
        incluidas = [
            t
            for t in self._de_la_jornada(fecha_jornada)
            if t.estado in ESTADOS_CONSOLIDABLES and t.reporte_id is None
        ]
        exigir_transacciones(tipo, len(incluidas), resumen.parciales_vigentes)
        consolidado = consolidar_movimientos(incluidas)
        reporte = ReporteDiario(
            id=uuid.uuid4(),
            fecha_jornada=fecha_jornada,
            corte=corte,
            estado=EstadoReporte.CONSOLIDADO,
            tipo=tipo,
            numero=resumen.ultimo_numero + 1,
            numero_transacciones=consolidado.numero_transacciones,
            total_debito=consolidado.total_debito,
            total_credito=consolidado.total_credito,
            valor_total=consolidado.valor_total,
            movimientos=consolidado.movimientos,
            subtotales=consolidado.subtotales,
            transacciones=tuple(t.id for t in incluidas),
            creado_en=datetime.now(UTC),
        )
        self._reportes[reporte.id] = reporte
        self._cupos[reporte.id] = cupo
        vinculo = datos_vinculo(reporte.id, fecha_jornada, tipo, reporte.numero)
        for t in incluidas:
            self.transacciones._por_id[t.id] = replace(t, reporte_id=reporte.id)
            self.transacciones._registrar(t.id, TipoEvento.CONSOLIDADA, usuario_id, despues=vinculo)
        self._registrar(reporte.id, EstadoReporte.CONSOLIDADO, usuario_id, despues=datos_consolidado(consolidado))
        return reporte

    def obtener(self, id_reporte: uuid.UUID) -> ReporteDiario | None:
        return self._reportes.get(id_reporte)

    def listar(self, estado: EstadoReporte | None = None, fecha_jornada: date | None = None) -> list[ReporteDiario]:
        reportes = sorted(self._reportes.values(), key=lambda r: r.creado_en, reverse=True)
        return [
            r
            for r in reportes
            if (estado is None or r.estado is estado) and (fecha_jornada is None or r.fecha_jornada == fecha_jornada)
        ]

    def _cargar(self, id_reporte: uuid.UUID) -> ReporteDiario:
        reporte = self._reportes.get(id_reporte)
        if reporte is None:
            raise ReporteNoEncontradoError(f"No existe el reporte {id_reporte}.")
        return reporte

    def eventos(self, id_reporte: uuid.UUID) -> list[EventoReporte]:
        self._cargar(id_reporte)
        return [e for e in self._eventos if e.reporte_id == id_reporte]

    def exportar(self, id_reporte: uuid.UUID, *, usuario_id: int | None, generar: GeneradorArchivo) -> ReporteDiario:
        reporte = self._cargar(id_reporte)
        validar_exportacion(reporte.estado)
        incluidas = [t for t in self.transacciones._por_id.values() if t.reporte_id == id_reporte]
        incluidas.sort(key=lambda t: (t.creado_en, t.numero_factura))
        exigir_sin_alertas(t.estado for t in incluidas)
        ahora = datetime.now(UTC)
        archivo = generar_archivo(generar, reporte, incluidas, usuario_id, ahora)
        self._archivos[id_reporte] = archivo
        self._reportes[id_reporte] = replace(
            reporte,
            estado=EstadoReporte.EXPORTADO,
            exportado_en=ahora,
            exportado_por=usuario_id,
            archivo_sha256=archivo.sha256,
        )
        exportado = datos_exportacion(archivo)
        self._registrar(id_reporte, EstadoReporte.EXPORTADO, usuario_id, despues=exportado)
        vinculo = datos_vinculo(id_reporte, reporte.fecha_jornada, reporte.tipo, reporte.numero)
        for t in incluidas:
            self.transacciones._registrar(t.id, TipoEvento.EXPORTADA, usuario_id, despues=vinculo | exportado)
        return self._reportes[id_reporte]

    def archivo(self, id_reporte: uuid.UUID) -> ArchivoExportado | None:
        self._cargar(id_reporte)
        return self._archivos.get(id_reporte)

    def anular(self, id_reporte: uuid.UUID, *, motivo: str, usuario_id: int | None) -> ReporteDiario:
        motivo = exigir_motivo(motivo, "anular un reporte")
        reporte = self._cargar(id_reporte)
        cierre = self.resumen_jornada(reporte.fecha_jornada).cierre_id
        validar_anulacion(reporte.estado, reporte.tipo, cierre is not None)
        liberada = {"reporte_id": str(id_reporte), "reporte_estado": EstadoReporte.ANULADO.value}
        for t in list(self.transacciones._por_id.values()):
            if t.reporte_id == id_reporte:
                self.transacciones._por_id[t.id] = replace(t, reporte_id=None)
                self.transacciones._registrar(t.id, TipoEvento.LIBERADA, usuario_id, motivo=motivo, despues=liberada)
        self._reportes[id_reporte] = replace(reporte, estado=EstadoReporte.ANULADO)
        self._registrar(id_reporte, EstadoReporte.ANULADO, usuario_id, motivo=motivo)
        return self._reportes[id_reporte]
