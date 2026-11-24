"""Repositorio de reportes en memoria. SOLO para pruebas; misma interfaz que RepositorioReportesSQL.

Comparte el estado con un RepositorioMemoria de transacciones, como en SQL comparten la base.
"""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from itertools import count

from repositorio_memoria import RepositorioMemoria

from agente.domain.enums import EstadoReporte, EstadoTransaccion, TipoEvento
from agente.domain.errors import ConsolidacionBloqueadaError, ReporteNoEncontradoError, SinTransaccionesError
from agente.domain.reportes import (
    ESTADOS_CONSOLIDABLES,
    ArchivoExportado,
    EventoReporte,
    ReporteDiario,
    consolidar_movimientos,
    exigir_sin_alertas,
    validar_exportacion,
)
from agente.infrastructure.db.reportes import (
    GeneradorArchivo,
    datos_consolidado,
    datos_exportacion,
    generar_archivo,
)


class RepositorioReportesMemoria:
    def __init__(self, transacciones: RepositorioMemoria) -> None:
        self.transacciones = transacciones
        self._reportes: dict[uuid.UUID, ReporteDiario] = {}
        self._eventos: list[EventoReporte] = []
        self._archivos: dict[uuid.UUID, ArchivoExportado] = {}
        self._ids_evento = count(1)

    def _registrar(self, reporte_id, tipo, usuario_id, *, motivo=None, despues=None) -> None:
        self._eventos.append(
            EventoReporte(next(self._ids_evento), reporte_id, tipo, usuario_id, datetime.now(UTC), motivo, despues)
        )

    def consolidar(self, *, corte: datetime, fecha_jornada: date, usuario_id: int | None = None) -> ReporteDiario:
        todas = sorted(self.transacciones._por_id.values(), key=lambda t: (t.creado_en, t.numero_factura))
        hasta_el_corte = [t for t in todas if t.creado_en <= corte]
        pendientes = sum(1 for t in hasta_el_corte if t.estado is EstadoTransaccion.EN_ALERTA)
        if pendientes:
            raise ConsolidacionBloqueadaError(pendientes)
        incluidas = [t for t in hasta_el_corte if t.estado in ESTADOS_CONSOLIDABLES and t.reporte_id is None]
        if not incluidas:
            raise SinTransaccionesError(
                "No hay transacciones aprobadas sin reporte hasta el corte: no se genera un reporte vacío."
            )
        consolidado = consolidar_movimientos(incluidas)
        reporte = ReporteDiario(
            id=uuid.uuid4(),
            fecha_jornada=fecha_jornada,
            corte=corte,
            estado=EstadoReporte.CONSOLIDADO,
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
        vinculo = {"reporte_id": str(reporte.id), "fecha_jornada": fecha_jornada.isoformat()}
        for t in incluidas:
            self.transacciones._por_id[t.id] = replace(t, reporte_id=reporte.id)
            self.transacciones._registrar(t.id, TipoEvento.CONSOLIDADA, usuario_id, despues=vinculo)
        self._registrar(reporte.id, EstadoReporte.CONSOLIDADO, usuario_id, despues=datos_consolidado(consolidado))
        return reporte

    def obtener(self, id_reporte: uuid.UUID) -> ReporteDiario | None:
        return self._reportes.get(id_reporte)

    def listar(self, estado: EstadoReporte | None = None) -> list[ReporteDiario]:
        reportes = sorted(self._reportes.values(), key=lambda r: r.creado_en, reverse=True)
        return [r for r in reportes if estado is None or r.estado is estado]

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
        self._registrar(id_reporte, EstadoReporte.EXPORTADO, usuario_id, despues=datos_exportacion(archivo))
        return self._reportes[id_reporte]

    def archivo(self, id_reporte: uuid.UUID) -> ArchivoExportado | None:
        self._cargar(id_reporte)
        return self._archivos.get(id_reporte)
