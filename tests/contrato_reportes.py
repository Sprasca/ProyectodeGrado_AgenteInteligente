"""Pruebas de contrato de RepositorioReportes (RF06, RF07).

Las heredan test_repositorio_reportes_memoria.py (unitarias) y test_repositorio_reportes_sql.py
(integración, sobre una base temporal). Cada módulo define los fixtures:
- `transacciones`: el repositorio de transacciones que comparte el estado con `reportes`;
- `reportes`: el repositorio de reportes;
- `id_usuario`: un usuario que exista; `nuevo_numero`: números de factura únicos.
"""

import hashlib
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fabricas import partidas_correctas, resultado_alerta_con_asiento, resultado_aprobado

from agente.domain.enums import EstadoReporte, EstadoTransaccion, Naturaleza, TipoEvento
from agente.domain.errors import (
    ConsolidacionBloqueadaError,
    ExportacionFallidaError,
    ReporteNoEncontradoError,
    SinTransaccionesError,
    TransicionInvalidaError,
)

JORNADA = date(2026, 11, 24)
MOTIVO = "Motivo de prueba del contrato"


def _aprobada(transacciones, catalogo, numero):
    return transacciones.guardar(resultado_aprobado(catalogo, numero))


def _corte(transacciones, *ids):
    """El instante de recepción más reciente de esas transacciones (reloj de la propia base)."""
    return max(transacciones.obtener(i).creado_en for i in ids)


class ContratoReportes:
    # --- qué entra en el reporte ------------------------------------------------------------------

    def test_consolida_las_aprobadas_y_deja_fuera_las_rechazadas(
        self, transacciones, reportes, catalogo, nuevo_numero, id_usuario
    ):
        automatica = _aprobada(transacciones, catalogo, nuevo_numero())
        por_contador = transacciones.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        transacciones.aprobar(por_contador, usuario_id=id_usuario)
        corregida = transacciones.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        transacciones.corregir(corregida, partidas_correctas(catalogo), motivo=MOTIVO, usuario_id=id_usuario)
        rechazada = _aprobada(transacciones, catalogo, nuevo_numero())
        transacciones.rechazar(rechazada, motivo=MOTIVO, usuario_id=id_usuario)

        corte = _corte(transacciones, automatica, por_contador, corregida, rechazada)
        reporte = reportes.consolidar(corte=corte, fecha_jornada=JORNADA, usuario_id=id_usuario)

        assert reporte.estado is EstadoReporte.CONSOLIDADO
        assert (reporte.fecha_jornada, reporte.corte) == (JORNADA, corte)
        assert set(reporte.transacciones) == {automatica, por_contador, corregida}
        assert reporte.numero_transacciones == 3
        assert reporte.total_debito == reporte.total_credito > 0
        assert reporte.valor_total == sum(transacciones.obtener(i).valor_total for i in reporte.transacciones)
        for id_ in reporte.transacciones:
            assert transacciones.obtener(id_).reporte_id == reporte.id
        assert transacciones.obtener(rechazada).reporte_id is None
        assert transacciones.obtener(rechazada).estado is EstadoTransaccion.RECHAZADA

    def test_agrupa_por_codigo_y_subtotaliza_por_cuenta(self, transacciones, reportes, catalogo, nuevo_numero):
        ids = [_aprobada(transacciones, catalogo, nuevo_numero()) for _ in range(2)]
        reporte = reportes.consolidar(corte=_corte(transacciones, *ids), fecha_jornada=JORNADA)

        esperado: dict[str, list[Decimal]] = {}
        for id_ in ids:
            for p in transacciones.obtener(id_).partidas:
                totales = esperado.setdefault(p.cuenta, [Decimal(0), Decimal(0)])
                totales[0 if p.naturaleza is Naturaleza.DEBITO else 1] += p.valor
        assert {m.codigo: [m.debito, m.credito] for m in reporte.movimientos} == esperado
        assert [m.codigo for m in reporte.movimientos] == sorted(esperado)
        for subtotal in reporte.subtotales:
            del_mismo = [m for m in reporte.movimientos if m.codigo[:4] == subtotal.cuenta]
            assert subtotal.debito == sum((m.debito for m in del_mismo), Decimal(0))
            assert subtotal.credito == sum((m.credito for m in del_mismo), Decimal(0))
        assert sum(s.debito for s in reporte.subtotales) == reporte.total_debito

    def test_lo_recibido_despues_del_corte_queda_para_el_siguiente_reporte(
        self, transacciones, reportes, catalogo, nuevo_numero
    ):
        antes = _aprobada(transacciones, catalogo, nuevo_numero())
        corte = _corte(transacciones, antes)
        time.sleep(0.02)  # que el reloj avance: lo siguiente se recibe estrictamente después del corte
        despues = _aprobada(transacciones, catalogo, nuevo_numero())
        transacciones.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))  # alerta posterior al corte

        reporte = reportes.consolidar(corte=corte, fecha_jornada=JORNADA)
        assert reporte.transacciones == (antes,)
        assert transacciones.obtener(despues).reporte_id is None

    # --- bloqueos y reporte vacío -----------------------------------------------------------------

    def test_una_alerta_sin_resolver_hasta_el_corte_bloquea_y_no_guarda_nada(
        self, transacciones, reportes, catalogo, nuevo_numero
    ):
        aprobada = _aprobada(transacciones, catalogo, nuevo_numero())
        alertas = [transacciones.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero())) for _ in range(2)]
        with pytest.raises(ConsolidacionBloqueadaError, match="hay 2 transacción") as error:
            reportes.consolidar(corte=_corte(transacciones, aprobada, *alertas), fecha_jornada=JORNADA)
        assert error.value.pendientes == 2
        assert reportes.listar() == []
        assert transacciones.obtener(aprobada).reporte_id is None
        assert [e.tipo for e in transacciones.eventos(aprobada)] == [TipoEvento.RECIBIDA, TipoEvento.CLASIFICADA]

    def test_sin_transacciones_no_se_crea_un_reporte_vacio(self, transacciones, reportes, catalogo, nuevo_numero):
        rechazada = _aprobada(transacciones, catalogo, nuevo_numero())
        transacciones.rechazar(rechazada, motivo=MOTIVO)
        with pytest.raises(SinTransaccionesError, match="reporte vacío"):
            reportes.consolidar(corte=_corte(transacciones, rechazada), fecha_jornada=JORNADA)
        assert reportes.listar() == []

    # --- unicidad y trazabilidad ------------------------------------------------------------------

    def test_una_transaccion_queda_en_un_solo_reporte(self, transacciones, reportes, catalogo, nuevo_numero):
        primera = _aprobada(transacciones, catalogo, nuevo_numero())
        uno = reportes.consolidar(corte=_corte(transacciones, primera), fecha_jornada=JORNADA)
        with pytest.raises(SinTransaccionesError):
            reportes.consolidar(corte=_corte(transacciones, primera), fecha_jornada=JORNADA)
        segunda = _aprobada(transacciones, catalogo, nuevo_numero())
        dos = reportes.consolidar(corte=_corte(transacciones, segunda), fecha_jornada=JORNADA)
        assert (uno.transacciones, dos.transacciones) == ((primera,), (segunda,))
        assert {r.id for r in reportes.listar()} == {uno.id, dos.id}

    def test_eventos_con_usuario_y_en_cada_transaccion(
        self, transacciones, reportes, catalogo, nuevo_numero, id_usuario
    ):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA, usuario_id=id_usuario)

        (evento,) = reportes.eventos(reporte.id)
        assert (evento.tipo, evento.usuario_id) == (EstadoReporte.CONSOLIDADO, id_usuario)
        assert evento.fecha is not None
        assert evento.despues["numero_transacciones"] == 1
        consolidada = transacciones.eventos(id_)[-1]
        assert (consolidada.tipo, consolidada.usuario_id) == (TipoEvento.CONSOLIDADA, id_usuario)
        assert consolidada.despues == {"reporte_id": str(reporte.id), "fecha_jornada": JORNADA.isoformat()}

    def test_el_cierre_programado_consolida_sin_usuario(self, transacciones, reportes, catalogo, nuevo_numero):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA, usuario_id=None)
        assert reportes.eventos(reporte.id)[0].usuario_id is None
        assert transacciones.eventos(id_)[-1].usuario_id is None

    # --- con reporte vigente no se corrige ni se rechaza (requisito 4) ----------------------------------

    @pytest.mark.parametrize("accion", ["corregir", "rechazar"])
    def test_con_reporte_vigente_no_se_corrige_ni_se_rechaza(
        self, transacciones, reportes, catalogo, nuevo_numero, id_usuario, accion
    ):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())  # APROBADA_AUTOMATICA: hoy sí es corregible
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA)
        antes = transacciones.obtener(id_)
        eventos_antes = len(transacciones.eventos(id_))

        with pytest.raises(TransicionInvalidaError, match=f"pertenece al reporte {reporte.id}"):
            if accion == "corregir":
                transacciones.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO, usuario_id=id_usuario)
            else:
                transacciones.rechazar(id_, motivo=MOTIVO, usuario_id=id_usuario)

        assert transacciones.obtener(id_) == antes
        assert len(transacciones.eventos(id_)) == eventos_antes

    # --- exportación (RF07) -----------------------------------------------------------------------------

    def test_exportar_genera_el_archivo_y_registra_autor_fecha_y_sha256(
        self, transacciones, reportes, catalogo, nuevo_numero, id_usuario
    ):
        ids = [_aprobada(transacciones, catalogo, nuevo_numero()) for _ in range(2)]
        reporte = reportes.consolidar(corte=_corte(transacciones, *ids), fecha_jornada=JORNADA)
        assert reportes.archivo(reporte.id) is None
        recibido = {}

        def generar(r, incluidas, autor, fecha):
            recibido.update(reporte=r, ids=[t.id for t in incluidas], autor=autor, fecha=fecha)
            return f"<xml>{r.id}</xml>".encode()

        antes = datetime.now(UTC)
        exportado = reportes.exportar(reporte.id, usuario_id=id_usuario, generar=generar)

        contenido = f"<xml>{reporte.id}</xml>".encode()
        assert exportado.estado is EstadoReporte.EXPORTADO
        assert exportado.exportado_por == id_usuario
        assert exportado.exportado_en == recibido["fecha"]
        assert abs((exportado.exportado_en - antes).total_seconds()) < 60
        assert exportado.archivo_sha256 == hashlib.sha256(contenido).hexdigest()
        assert recibido["ids"] == list(reporte.transacciones) and recibido["autor"] == id_usuario
        archivo = reportes.archivo(reporte.id)
        assert (archivo.contenido, archivo.sha256) == (contenido, exportado.archivo_sha256)
        assert archivo.nombre == f"reporte-diario-{JORNADA.isoformat()}-{reporte.id.hex[:8]}.xml"
        assert reportes.obtener(reporte.id) == exportado
        evento = reportes.eventos(reporte.id)[-1]
        assert (evento.tipo, evento.usuario_id) == (EstadoReporte.EXPORTADO, id_usuario)
        assert evento.despues == {"archivo": archivo.nombre, "bytes": len(contenido), "sha256": archivo.sha256}

    def test_un_reporte_no_se_exporta_dos_veces(self, transacciones, reportes, catalogo, nuevo_numero, id_usuario):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA)
        primero = reportes.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<uno/>")
        with pytest.raises(TransicionInvalidaError, match="no se exporta dos veces"):
            reportes.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<otro/>")
        assert reportes.archivo(reporte.id).contenido == b"<uno/>"
        assert reportes.obtener(reporte.id) == primero
        assert [e.tipo for e in reportes.eventos(reporte.id)] == [EstadoReporte.CONSOLIDADO, EstadoReporte.EXPORTADO]

    def test_si_falla_la_generacion_el_reporte_sigue_consolidado(
        self, transacciones, reportes, catalogo, nuevo_numero, id_usuario
    ):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA)

        def falla(*_):
            raise OSError("disco lleno")

        with pytest.raises(ExportacionFallidaError, match="disco lleno"):
            reportes.exportar(reporte.id, usuario_id=id_usuario, generar=falla)
        assert reportes.obtener(reporte.id) == reporte
        assert reportes.archivo(reporte.id) is None
        assert [e.tipo for e in reportes.eventos(reporte.id)] == [EstadoReporte.CONSOLIDADO]
        # Se puede volver a intentar.
        assert reportes.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<ok/>").estado is (
            EstadoReporte.EXPORTADO
        )

    def test_exportar_un_reporte_inexistente(self, reportes, id_usuario):
        with pytest.raises(ReporteNoEncontradoError):
            reportes.exportar(uuid.uuid4(), usuario_id=id_usuario, generar=lambda *_: b"")
        with pytest.raises(ReporteNoEncontradoError):
            reportes.archivo(uuid.uuid4())

    def test_una_transaccion_exportada_no_se_corrige(
        self, transacciones, reportes, catalogo, nuevo_numero, id_usuario
    ):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA)
        reportes.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<x/>")
        with pytest.raises(TransicionInvalidaError, match="un reporte EXPORTADO no se puede anular"):
            transacciones.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO, usuario_id=id_usuario)

    # --- consulta ------------------------------------------------------------------------------------

    def test_obtener_y_listar_por_estado(self, transacciones, reportes, catalogo, nuevo_numero):
        id_ = _aprobada(transacciones, catalogo, nuevo_numero())
        reporte = reportes.consolidar(corte=_corte(transacciones, id_), fecha_jornada=JORNADA)
        assert reportes.obtener(reporte.id) == reporte
        assert [r.id for r in reportes.listar(EstadoReporte.CONSOLIDADO)] == [reporte.id]
        assert reportes.listar(EstadoReporte.EXPORTADO) == []

    def test_reporte_inexistente(self, reportes):
        assert reportes.obtener(uuid.uuid4()) is None
        with pytest.raises(ReporteNoEncontradoError):
            reportes.eventos(uuid.uuid4())
