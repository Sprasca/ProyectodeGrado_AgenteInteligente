"""Pruebas de contrato de RepositorioReportes (RF06, RF07).

Las heredan test_repositorio_reportes_memoria.py (unitarias) y test_repositorio_reportes_sql.py
(integración, sobre una base temporal). Cada módulo define los fixtures:
- `transacciones`: el repositorio de transacciones que comparte el estado con `reportes`;
- `reportes`: el repositorio de reportes;
- `id_usuario`: un usuario que exista; `nuevo_numero`: números de factura únicos;
- `mover_a_jornada(id, fecha)`: cambia la jornada de una transacción, para que las pruebas no dependan de la
  hora a la que corren (la jornada real depende de la HORA_CIERRE de Colombia).
"""

import hashlib
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from fabricas import partidas_correctas, resultado_alerta_con_asiento, resultado_aprobado

from agente.domain.asiento import Partida
from agente.domain.enums import EstadoReporte, EstadoTransaccion, Naturaleza, TipoEvento, TipoReporte
from agente.domain.errors import (
    ConsolidacionBloqueadaError,
    ExportacionFallidaError,
    JornadaCerradaError,
    LimiteDeReportesError,
    MotivoInvalidoError,
    ReporteNoEncontradoError,
    SinTransaccionesError,
    TransicionInvalidaError,
)
from agente.domain.jornada import corte_de_la_jornada

JORNADA = date(2026, 11, 24)
OTRA_JORNADA = JORNADA + timedelta(days=1)
CORTE_CIERRE = corte_de_la_jornada(JORNADA, time(18, 0))
MOTIVO = "Motivo de prueba del contrato"
PARCIAL, CIERRE = TipoReporte.PARCIAL, TipoReporte.CIERRE


class ContratoReportes:
    # --- ayudas -------------------------------------------------------------------------------------------

    @pytest.fixture(autouse=True)
    def _ayudas(self, transacciones, reportes, catalogo, nuevo_numero, mover_a_jornada):
        self.t, self.r, self.catalogo, self.numero, self.mover = (
            transacciones,
            reportes,
            catalogo,
            nuevo_numero,
            mover_a_jornada,
        )

    def aprobada(self, jornada=JORNADA):
        id_ = self.t.guardar(resultado_aprobado(self.catalogo, self.numero()))
        self.mover(id_, jornada)
        return id_

    def en_alerta(self, jornada=JORNADA):
        id_ = self.t.guardar(resultado_alerta_con_asiento(self.catalogo, self.numero()))
        self.mover(id_, jornada)
        return id_

    def parcial(self, jornada=JORNADA, usuario_id=None):
        return self.r.consolidar(fecha_jornada=jornada, tipo=PARCIAL, corte=datetime.now(UTC), usuario_id=usuario_id)

    def cierre(self, jornada=JORNADA, usuario_id=None):
        corte = corte_de_la_jornada(jornada, time(18, 0))
        return self.r.consolidar(fecha_jornada=jornada, tipo=CIERRE, corte=corte, usuario_id=usuario_id)

    # --- qué entra en el reporte ------------------------------------------------------------------

    def test_consolida_las_aprobadas_y_deja_fuera_las_rechazadas(self, id_usuario):
        automatica = self.aprobada()
        por_contador = self.en_alerta()
        self.t.aprobar(por_contador, usuario_id=id_usuario)
        corregida = self.en_alerta()
        self.t.corregir(corregida, partidas_correctas(self.catalogo), motivo=MOTIVO, usuario_id=id_usuario)
        rechazada = self.aprobada()
        self.t.rechazar(rechazada, motivo=MOTIVO, usuario_id=id_usuario)

        reporte = self.parcial(usuario_id=id_usuario)

        assert reporte.estado is EstadoReporte.CONSOLIDADO
        assert (reporte.tipo, reporte.numero, reporte.fecha_jornada) == (PARCIAL, 1, JORNADA)
        assert set(reporte.transacciones) == {automatica, por_contador, corregida}
        assert reporte.numero_transacciones == 3
        assert reporte.total_debito == reporte.total_credito > 0
        assert reporte.valor_total == sum(self.t.obtener(i).valor_total for i in reporte.transacciones)
        for id_ in reporte.transacciones:
            assert self.t.obtener(id_).reporte_id == reporte.id
        assert self.t.obtener(rechazada).reporte_id is None
        assert self.t.obtener(rechazada).estado is EstadoTransaccion.RECHAZADA

    def test_agrupa_por_codigo_y_subtotaliza_por_cuenta(self):
        ids = [self.aprobada() for _ in range(2)]
        reporte = self.parcial()

        esperado: dict[str, list[Decimal]] = {}
        for id_ in ids:
            for p in self.t.obtener(id_).partidas:
                totales = esperado.setdefault(p.cuenta, [Decimal(0), Decimal(0)])
                totales[0 if p.naturaleza is Naturaleza.DEBITO else 1] += p.valor
        assert {m.codigo: [m.debito, m.credito] for m in reporte.movimientos} == esperado
        assert [m.codigo for m in reporte.movimientos] == sorted(esperado)
        for subtotal in reporte.subtotales:
            del_mismo = [m for m in reporte.movimientos if m.codigo[:4] == subtotal.cuenta]
            assert subtotal.debito == sum((m.debito for m in del_mismo), Decimal(0))
            assert subtotal.credito == sum((m.credito for m in del_mismo), Decimal(0))
        assert sum(s.debito for s in reporte.subtotales) == reporte.total_debito

    def test_solo_entran_las_de_su_jornada(self):
        de_hoy = self.aprobada()
        de_manana = self.aprobada(OTRA_JORNADA)  # p. ej., recibida después de la hora de cierre
        self.en_alerta(OTRA_JORNADA)  # una alerta de otra jornada no bloquea esta

        reporte = self.cierre()
        assert reporte.transacciones == (de_hoy,)
        assert reporte.corte == CORTE_CIERRE
        assert self.t.obtener(de_manana).reporte_id is None

    # --- bloqueos y reporte vacío -----------------------------------------------------------------

    @pytest.mark.parametrize("tipo", [PARCIAL, CIERRE])
    def test_una_alerta_de_la_jornada_bloquea_y_no_guarda_nada(self, tipo):
        aprobada = self.aprobada()
        self.en_alerta()
        self.en_alerta()
        with pytest.raises(ConsolidacionBloqueadaError, match="hay 2 transacción") as error:
            self.parcial() if tipo is PARCIAL else self.cierre()
        assert error.value.pendientes == 2
        assert self.r.listar() == []
        assert self.t.obtener(aprobada).reporte_id is None
        assert [e.tipo for e in self.t.eventos(aprobada)] == [TipoEvento.RECIBIDA, TipoEvento.CLASIFICADA]

    def test_un_parcial_nunca_queda_vacio(self):
        rechazada = self.aprobada()
        self.t.rechazar(rechazada, motivo=MOTIVO)
        with pytest.raises(SinTransaccionesError, match="reporte vacío"):
            self.parcial()
        assert self.r.listar() == []

    def test_sin_transacciones_ni_parciales_no_hay_nada_que_cerrar(self):
        with pytest.raises(SinTransaccionesError, match="nada que cerrar"):
            self.cierre()
        assert self.r.listar() == []

    def test_el_cierre_puede_quedar_sin_transacciones_propias_si_hay_parciales(self):
        self.aprobada()
        self.parcial()
        cierre = self.cierre()
        assert (cierre.tipo, cierre.numero, cierre.numero_transacciones) == (CIERRE, 2, 0)
        assert cierre.total_debito == cierre.total_credito == 0

    # --- tipos, números y límite por jornada ---------------------------------------------------------

    def test_una_transaccion_queda_en_un_solo_reporte_y_los_numeros_son_consecutivos(self):
        primera = self.aprobada()
        uno = self.parcial()
        with pytest.raises(SinTransaccionesError):
            self.parcial()
        segunda = self.aprobada()
        dos = self.parcial()
        tercera = self.aprobada()
        tres = self.cierre()
        assert [(r.tipo, r.numero, r.transacciones) for r in (uno, dos, tres)] == [
            (PARCIAL, 1, (primera,)),
            (PARCIAL, 2, (segunda,)),
            (CIERRE, 3, (tercera,)),
        ]
        assert {r.id for r in self.r.listar()} == {uno.id, dos.id, tres.id}

    def test_los_numeros_empiezan_en_1_en_cada_jornada(self):
        self.aprobada()
        self.aprobada(OTRA_JORNADA)
        assert self.parcial().numero == 1
        assert self.parcial(OTRA_JORNADA).numero == 1

    def test_maximo_4_parciales_y_el_quinto_cupo_es_del_cierre(self):
        for _ in range(4):
            self.aprobada()
            self.parcial()
        pendiente = self.aprobada()
        with pytest.raises(LimiteDeReportesError, match="quinto cupo es del reporte de cierre"):
            self.parcial()
        assert self.t.obtener(pendiente).reporte_id is None
        cierre = self.cierre()  # el límite nunca impide cerrar el día
        assert (cierre.numero, cierre.transacciones) == (5, (pendiente,))
        assert self.r.resumen_jornada(JORNADA).reportes_vigentes == 5

    def test_un_parcial_anulado_libera_su_cupo_pero_no_su_numero(self, id_usuario):
        parciales = []
        for _ in range(4):
            self.aprobada()
            parciales.append(self.parcial())
        self.r.anular(parciales[1].id, motivo=MOTIVO, usuario_id=id_usuario)
        otro = self.parcial()  # toma la transacción liberada
        assert (otro.numero, otro.transacciones) == (5, parciales[1].transacciones)

    def test_un_solo_cierre_vigente_y_despues_no_hay_mas_reportes(self):
        self.aprobada()
        cierre = self.cierre()
        self.aprobada()
        with pytest.raises(JornadaCerradaError, match=str(cierre.id)):
            self.cierre()
        with pytest.raises(JornadaCerradaError, match="no admite más reportes"):
            self.parcial()
        assert [r.id for r in self.r.listar()] == [cierre.id]

    def test_anular_el_cierre_reabre_la_jornada(self, id_usuario):
        primera = self.aprobada()
        cierre = self.cierre()
        self.r.anular(cierre.id, motivo=MOTIVO, usuario_id=id_usuario)
        segunda = self.aprobada()
        nuevo = self.cierre()
        assert (nuevo.numero, set(nuevo.transacciones)) == (2, {primera, segunda})

    def test_un_parcial_no_se_anula_si_la_jornada_tiene_cierre(self, id_usuario):
        self.aprobada()
        parcial = self.parcial()
        self.aprobada()
        cierre = self.cierre()
        with pytest.raises(TransicionInvalidaError, match="anule primero el cierre"):
            self.r.anular(parcial.id, motivo=MOTIVO, usuario_id=id_usuario)
        self.r.anular(cierre.id, motivo=MOTIVO, usuario_id=id_usuario)
        assert self.r.anular(parcial.id, motivo=MOTIVO, usuario_id=id_usuario).estado is EstadoReporte.ANULADO

    def test_resumen_de_la_jornada(self, id_usuario):
        vacio = self.r.resumen_jornada(JORNADA)
        assert (vacio.reportes_vigentes, vacio.cierre_id, vacio.ultimo_numero) == (0, None, 0)
        self.aprobada()
        parcial = self.parcial()
        self.aprobada()
        self.en_alerta()
        self.t.rechazar(self.aprobada(), motivo=MOTIVO)  # las RECHAZADAS no cuentan como recibidas
        self.r.anular(parcial.id, motivo=MOTIVO, usuario_id=id_usuario)
        # El anulado no cuenta como vigente, pero su número ya se usó.
        resumen = self.r.resumen_jornada(JORNADA)
        assert resumen.fecha_jornada == JORNADA
        assert (resumen.reportes_vigentes, resumen.parciales_vigentes, resumen.ultimo_numero) == (0, 0, 1)
        assert (resumen.alertas_pendientes, resumen.transacciones_sin_reporte) == (1, 2)
        assert resumen.transacciones_recibidas == 3

    # --- trazabilidad ------------------------------------------------------------------------------

    def test_eventos_con_usuario_y_en_cada_transaccion(self, id_usuario):
        id_ = self.aprobada()
        reporte = self.parcial(usuario_id=id_usuario)

        (evento,) = self.r.eventos(reporte.id)
        assert (evento.tipo, evento.usuario_id) == (EstadoReporte.CONSOLIDADO, id_usuario)
        assert evento.fecha is not None
        assert evento.despues["numero_transacciones"] == 1
        consolidada = self.t.eventos(id_)[-1]
        assert (consolidada.tipo, consolidada.usuario_id) == (TipoEvento.CONSOLIDADA, id_usuario)
        assert consolidada.despues == {
            "reporte_id": str(reporte.id),
            "fecha_jornada": JORNADA.isoformat(),
            "tipo": "PARCIAL",
            "numero": 1,
        }

    def test_el_cierre_programado_consolida_sin_usuario(self):
        id_ = self.aprobada()
        reporte = self.cierre(usuario_id=None)
        assert self.r.eventos(reporte.id)[0].usuario_id is None
        assert self.t.eventos(id_)[-1].usuario_id is None

    # --- con reporte vigente no se corrige ni se rechaza (requisito 4) ----------------------------------

    @pytest.mark.parametrize("accion", ["corregir", "rechazar"])
    def test_con_reporte_vigente_no_se_corrige_ni_se_rechaza(self, id_usuario, accion):
        id_ = self.aprobada()  # APROBADA_AUTOMATICA: sin reporte sí sería corregible
        reporte = self.parcial()
        antes = self.t.obtener(id_)
        eventos_antes = len(self.t.eventos(id_))

        with pytest.raises(TransicionInvalidaError, match=f"pertenece al reporte {reporte.id}"):
            if accion == "corregir":
                self.t.corregir(id_, partidas_correctas(self.catalogo), motivo=MOTIVO, usuario_id=id_usuario)
            else:
                self.t.rechazar(id_, motivo=MOTIVO, usuario_id=id_usuario)

        assert self.t.obtener(id_) == antes
        assert len(self.t.eventos(id_)) == eventos_antes

    def test_el_bloqueo_por_reporte_se_revisa_antes_que_las_cuentas(self, id_usuario):
        """Con cuentas que no existen, el motivo del rechazo sigue siendo el reporte, no la cuenta."""
        id_ = self.aprobada()
        reporte = self.parcial()
        inexistentes = [
            Partida("999999", Naturaleza.DEBITO, Decimal("119000.00"), "No existe"),
            Partida("4135", Naturaleza.CREDITO, Decimal("119000.00"), "Ingreso"),
        ]
        with pytest.raises(TransicionInvalidaError, match=f"pertenece al reporte {reporte.id}"):
            self.t.corregir(id_, inexistentes, motivo=MOTIVO, usuario_id=id_usuario)

    # --- exportación (RF07) -----------------------------------------------------------------------------

    def test_exportar_genera_el_archivo_y_registra_autor_fecha_y_sha256(self, id_usuario):
        for _ in range(2):
            self.aprobada()
        reporte = self.parcial()
        assert self.r.archivo(reporte.id) is None
        recibido = {}

        def generar(r, incluidas, autor, fecha):
            recibido.update(reporte=r, ids=[t.id for t in incluidas], autor=autor, fecha=fecha)
            return f"<xml>{r.id}</xml>".encode()

        antes = datetime.now(UTC)
        exportado = self.r.exportar(reporte.id, usuario_id=id_usuario, generar=generar)

        contenido = f"<xml>{reporte.id}</xml>".encode()
        assert exportado.estado is EstadoReporte.EXPORTADO
        assert exportado.exportado_por == id_usuario
        assert exportado.exportado_en == recibido["fecha"]
        assert abs((exportado.exportado_en - antes).total_seconds()) < 60
        assert exportado.archivo_sha256 == hashlib.sha256(contenido).hexdigest()
        assert recibido["ids"] == list(reporte.transacciones) and recibido["autor"] == id_usuario
        archivo = self.r.archivo(reporte.id)
        assert (archivo.contenido, archivo.sha256) == (contenido, exportado.archivo_sha256)
        assert archivo.nombre == f"reporte-diario-{JORNADA.isoformat()}-1-parcial-{reporte.id.hex[:8]}.xml"
        assert self.r.obtener(reporte.id) == exportado
        evento = self.r.eventos(reporte.id)[-1]
        assert (evento.tipo, evento.usuario_id) == (EstadoReporte.EXPORTADO, id_usuario)
        assert evento.despues == {"archivo": archivo.nombre, "bytes": len(contenido), "sha256": archivo.sha256}

    def test_exportar_deja_el_evento_exportada_en_cada_transaccion(self, id_usuario):
        ids = [self.aprobada() for _ in range(2)]
        reporte = self.parcial()
        exportado = self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<x/>")
        for id_ in ids:
            evento = self.t.eventos(id_)[-1]
            assert (evento.tipo, evento.usuario_id) == (TipoEvento.EXPORTADA, id_usuario)
            assert evento.despues["reporte_id"] == str(reporte.id)
            assert evento.despues["sha256"] == exportado.archivo_sha256

    def test_un_reporte_no_se_exporta_dos_veces(self, id_usuario):
        id_ = self.aprobada()
        reporte = self.parcial()
        primero = self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<uno/>")
        with pytest.raises(TransicionInvalidaError, match="no se exporta dos veces"):
            self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<otro/>")
        assert self.r.archivo(reporte.id).contenido == b"<uno/>"
        assert self.r.obtener(reporte.id) == primero
        assert [e.tipo for e in self.r.eventos(reporte.id)] == [EstadoReporte.CONSOLIDADO, EstadoReporte.EXPORTADO]
        assert [e.tipo for e in self.t.eventos(id_)].count(TipoEvento.EXPORTADA) == 1

    def test_si_falla_la_generacion_el_reporte_sigue_consolidado(self, id_usuario):
        id_ = self.aprobada()
        reporte = self.parcial()

        def falla(*_):
            raise OSError("disco lleno")

        with pytest.raises(ExportacionFallidaError, match="disco lleno"):
            self.r.exportar(reporte.id, usuario_id=id_usuario, generar=falla)
        assert self.r.obtener(reporte.id) == reporte
        assert self.r.archivo(reporte.id) is None
        assert [e.tipo for e in self.r.eventos(reporte.id)] == [EstadoReporte.CONSOLIDADO]
        assert TipoEvento.EXPORTADA not in [e.tipo for e in self.t.eventos(id_)]
        # Se puede volver a intentar.
        reintento = self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<ok/>")
        assert reintento.estado is EstadoReporte.EXPORTADO

    def test_exportar_un_reporte_inexistente(self, id_usuario):
        with pytest.raises(ReporteNoEncontradoError):
            self.r.exportar(uuid.uuid4(), usuario_id=id_usuario, generar=lambda *_: b"")
        with pytest.raises(ReporteNoEncontradoError):
            self.r.archivo(uuid.uuid4())

    def test_una_transaccion_exportada_no_se_corrige(self, id_usuario):
        id_ = self.aprobada()
        reporte = self.parcial()
        self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<x/>")
        with pytest.raises(TransicionInvalidaError, match="un reporte EXPORTADO no se puede anular"):
            self.t.corregir(id_, partidas_correctas(self.catalogo), motivo=MOTIVO, usuario_id=id_usuario)

    # --- anulación (ampliación del manual) ----------------------------------------------------------------

    def test_anular_libera_las_transacciones_y_conserva_la_lista_y_los_totales(self, id_usuario):
        ids = [self.aprobada() for _ in range(2)]
        reporte = self.parcial()

        motivo = "Se consolidó antes de corregir una factura"
        anulado = self.r.anular(reporte.id, motivo=motivo, usuario_id=id_usuario)

        assert anulado.estado is EstadoReporte.ANULADO
        assert anulado.transacciones == reporte.transacciones  # vínculo histórico
        assert (anulado.movimientos, anulado.subtotales, anulado.total_debito, anulado.numero_transacciones) == (
            reporte.movimientos,
            reporte.subtotales,
            reporte.total_debito,
            reporte.numero_transacciones,
        )
        assert self.r.obtener(reporte.id) == anulado
        evento = self.r.eventos(reporte.id)[-1]
        assert (evento.tipo, evento.usuario_id, evento.motivo) == (EstadoReporte.ANULADO, id_usuario, motivo)
        for id_ in ids:
            assert self.t.obtener(id_).reporte_id is None
            liberada = self.t.eventos(id_)[-1]
            assert (liberada.tipo, liberada.usuario_id) == (TipoEvento.LIBERADA, id_usuario)
            assert liberada.despues["reporte_id"] == str(reporte.id)

    def test_tras_anular_se_corrige_y_se_consolida_en_otro_reporte(self, id_usuario):
        id_ = self.aprobada()
        primero = self.parcial()
        self.r.anular(primero.id, motivo=MOTIVO, usuario_id=id_usuario)

        corregida = self.t.corregir(id_, partidas_correctas(self.catalogo), motivo=MOTIVO, usuario_id=id_usuario)
        assert corregida.estado is EstadoTransaccion.CORREGIDA_CONTADOR
        segundo = self.parcial()
        assert segundo.transacciones == (id_,)
        assert self.t.obtener(id_).reporte_id == segundo.id
        assert self.r.obtener(primero.id).transacciones == (id_,)  # el anulado la sigue listando

    def test_un_reporte_exportado_nunca_se_anula(self, id_usuario):
        id_ = self.aprobada()
        reporte = self.parcial()
        exportado = self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<x/>")
        with pytest.raises(TransicionInvalidaError, match="EXPORTADO nunca se anula"):
            self.r.anular(reporte.id, motivo=MOTIVO, usuario_id=id_usuario)
        assert self.r.obtener(reporte.id) == exportado
        assert self.t.obtener(id_).reporte_id == reporte.id

    def test_un_reporte_anulado_no_se_anula_ni_se_exporta(self, id_usuario):
        self.aprobada()
        reporte = self.parcial()
        self.r.anular(reporte.id, motivo=MOTIVO, usuario_id=id_usuario)
        with pytest.raises(TransicionInvalidaError, match="Solo se anula un reporte CONSOLIDADO"):
            self.r.anular(reporte.id, motivo=MOTIVO, usuario_id=id_usuario)
        with pytest.raises(TransicionInvalidaError, match="Solo se exporta un reporte CONSOLIDADO"):
            self.r.exportar(reporte.id, usuario_id=id_usuario, generar=lambda *_: b"<x/>")

    @pytest.mark.parametrize("motivo", ["", "   ", "corto"])
    def test_anular_exige_motivo(self, id_usuario, motivo):
        id_ = self.aprobada()
        reporte = self.parcial()
        with pytest.raises(MotivoInvalidoError):
            self.r.anular(reporte.id, motivo=motivo, usuario_id=id_usuario)
        assert self.r.obtener(reporte.id) == reporte
        assert self.t.obtener(id_).reporte_id == reporte.id

    def test_anular_un_reporte_inexistente(self, id_usuario):
        with pytest.raises(ReporteNoEncontradoError):
            self.r.anular(uuid.uuid4(), motivo=MOTIVO, usuario_id=id_usuario)

    # --- consulta ------------------------------------------------------------------------------------

    def test_obtener_y_listar_por_estado_y_jornada(self):
        self.aprobada()
        self.aprobada(OTRA_JORNADA)
        reporte = self.parcial()
        otro = self.parcial(OTRA_JORNADA)
        assert self.r.obtener(reporte.id) == reporte
        assert {r.id for r in self.r.listar(EstadoReporte.CONSOLIDADO)} == {reporte.id, otro.id}
        assert self.r.listar(EstadoReporte.EXPORTADO) == []
        assert [r.id for r in self.r.listar(fecha_jornada=OTRA_JORNADA)] == [otro.id]

    def test_reporte_inexistente(self):
        assert self.r.obtener(uuid.uuid4()) is None
        with pytest.raises(ReporteNoEncontradoError):
            self.r.eventos(uuid.uuid4())
