"""Pruebas de contrato de RepositorioNotificaciones (RF04).

Las heredan test_repositorio_notificaciones_memoria.py (unitarias) y test_repositorio_notificaciones_sql.py
(integración, sobre una base temporal: los destinatarios son TODOS los usuarios activos de cada rol).
Cada módulo define los fixtures:
- `notificaciones`: el repositorio;
- `usuarios`: {"contador", "auxiliar", "admin", "admin_inactivo"} → id, ya creados.
ContratoNotificacionesDeTransacciones pide además `transacciones` y `nuevo_numero`.
"""

import pytest
from fabricas import partidas_correctas, resultado_alerta_con_asiento, resultado_aprobado

from agente.domain.enums import ClaseNotificacion, TipoNotificacion
from agente.domain.errors import FacturaDuplicadaError, NotificacionNoEncontradaError, TransicionInvalidaError
from agente.domain.notificaciones import Causa

T = TipoNotificacion


def causa(tipo=T.ARCHIVO_NO_RECIBIDO, clave="archivo:a.xml", mensaje="No se recibió a.xml", **datos):
    return Causa(tipo, clave, mensaje, datos or None)


class ContratoNotificaciones:
    @pytest.fixture(autouse=True)
    def _ayudas(self, notificaciones, usuarios):
        self.n, self.u = notificaciones, usuarios

    def ids(self, usuario):
        return [x.id for x in self.n.listar(self.u[usuario])]

    # --- destinatarios ------------------------------------------------------------------------------------

    def test_una_tecnica_llega_solo_a_los_admin_activos(self):
        id_ = self.n.registrar(causa())
        assert self.ids("admin") == [id_]
        assert self.ids("admin_inactivo") == self.ids("contador") == self.ids("auxiliar") == []
        (vista,) = self.n.listar(self.u["admin"])
        assert (vista.tipo, vista.clase) == (T.ARCHIVO_NO_RECIBIDO, ClaseNotificacion.TECNICA)
        assert vista.mensaje == "No se recibió a.xml"
        assert (vista.leida_en, vista.atendida_en, vista.informativa) == (None, None, False)
        assert vista.se_atiende_a_mano is True

    def test_la_transaccion_en_alerta_es_para_resolver_del_contador_e_informativa_del_auxiliar(self):
        id_ = self.n.registrar(causa(T.TRANSACCION_EN_ALERTA, "transaccion:1", "FV-1 en alerta"))
        (contador,) = self.n.listar(self.u["contador"])
        (auxiliar,) = self.n.listar(self.u["auxiliar"])
        assert contador.id == auxiliar.id == id_
        assert (contador.clase, contador.informativa, auxiliar.informativa) == (ClaseNotificacion.CONTABLE, False, True)
        assert contador.se_atiende_a_mano is False
        assert self.ids("admin") == []

    def test_la_factura_duplicada_es_contable_para_el_auxiliar(self):
        self.n.registrar(causa(T.FACTURA_DUPLICADA, "archivo:dup.xml", "FV-1 enviada dos veces"))
        (vista,) = self.n.listar(self.u["auxiliar"])
        assert (vista.clase, vista.informativa) == (ClaseNotificacion.CONTABLE, False)
        assert self.ids("contador") == self.ids("admin") == []

    # --- una sola abierta por causa --------------------------------------------------------------------------

    def test_la_misma_causa_abierta_no_se_duplica_se_actualiza_y_vuelve_a_quedar_sin_leer(self):
        primera = self.n.registrar(causa(T.CONSOLIDACION_BLOQUEADA, "jornada:2026-11-25", "1 pendiente", pendientes=1))
        self.n.leer(self.u["contador"], primera)
        segunda = self.n.registrar(causa(T.CONSOLIDACION_BLOQUEADA, "jornada:2026-11-25", "3 pendientes", pendientes=3))
        assert segunda == primera
        (vista,) = self.n.listar(self.u["contador"])
        assert (vista.mensaje, vista.datos, vista.leida_en) == ("3 pendientes", {"pendientes": 3}, None)

    def test_atendida_la_causa_una_nueva_ocurrencia_es_otra_notificacion(self):
        primera = self.n.registrar(causa(T.DIRECTORIO_NO_DISPONIBLE, "directorio:entrada"))
        assert self.n.atender_causa(T.DIRECTORIO_NO_DISPONIBLE, "directorio:entrada", "Volvió") == 1
        assert self.n.atender_causa(T.DIRECTORIO_NO_DISPONIBLE, "directorio:entrada", "Volvió") == 0
        segunda = self.n.registrar(causa(T.DIRECTORIO_NO_DISPONIBLE, "directorio:entrada"))
        assert segunda != primera
        vistas = {v.id: v for v in self.n.listar(self.u["admin"])}
        assert vistas[primera].resolucion == "Volvió" and vistas[primera].atendida_en is not None
        assert vistas[primera].atendida_por is None  # se atendió sola
        assert vistas[segunda].atendida_en is None

    def test_existe_aunque_este_atendida(self):
        assert not self.n.existe(T.JORNADA_SIN_CIERRE, "jornada:2026-11-24")
        self.n.registrar(causa(T.JORNADA_SIN_CIERRE, "jornada:2026-11-24"))
        self.n.atender_causa(T.JORNADA_SIN_CIERRE, "jornada:2026-11-24", "Se cerró")
        assert self.n.existe(T.JORNADA_SIN_CIERRE, "jornada:2026-11-24")
        assert not self.n.existe(T.JORNADA_SIN_CIERRE, "jornada:2026-11-23")

    # --- bandeja de cada usuario -----------------------------------------------------------------------------

    def test_leer_contar_y_filtrar(self):
        uno = self.n.registrar(causa(clave="archivo:1.xml"))
        dos = self.n.registrar(causa(clave="archivo:2.xml"))
        admin = self.u["admin"]
        assert self.n.contar_no_leidas(admin) == 2
        assert self.n.leer(admin, uno).leida_en is not None
        assert self.n.contar_no_leidas(admin) == 1
        assert [v.id for v in self.n.listar(admin, no_leidas=True)] == [dos]
        self.n.atender(admin, dos)
        assert [v.id for v in self.n.listar(admin, no_atendidas=True)] == [uno]
        assert self.n.leer_todas(admin) == 1
        assert self.n.contar_no_leidas(admin) == 0 and self.n.leer_todas(admin) == 0

    def test_cada_usuario_solo_ve_y_lee_las_suyas(self):
        id_ = self.n.registrar(causa())
        with pytest.raises(NotificacionNoEncontradaError):
            self.n.leer(self.u["contador"], id_)
        with pytest.raises(NotificacionNoEncontradaError):
            self.n.atender(self.u["auxiliar"], id_)
        with pytest.raises(NotificacionNoEncontradaError):
            self.n.leer(self.u["admin"], 999_999)
        assert self.n.leer_todas(self.u["contador"]) == 0
        assert self.n.listar(self.u["admin"])[0].leida_en is None  # leer las del contador no tocó las del admin

    def test_leida_es_por_usuario(self):
        id_ = self.n.registrar(causa(T.REPORTE_DISPONIBLE, "reporte:1"))
        self.n.leer(self.u["contador"], id_)
        assert self.n.contar_no_leidas(self.u["contador"]) == 0
        assert self.n.contar_no_leidas(self.u["auxiliar"]) == 1

    # --- atención a mano --------------------------------------------------------------------------------------

    def test_atender_a_mano_una_que_no_se_resuelve_sola(self):
        id_ = self.n.registrar(causa())
        atendida = self.n.atender(self.u["admin"], id_)
        assert atendida.atendida_en is not None and atendida.atendida_por == self.u["admin"]
        assert atendida.resolucion == "Atendida a mano."
        with pytest.raises(TransicionInvalidaError, match="ya está atendida"):
            self.n.atender(self.u["admin"], id_)

    @pytest.mark.parametrize(
        ("tipo", "usuario"),
        [
            (T.TRANSACCION_EN_ALERTA, "contador"),
            (T.CONSOLIDACION_BLOQUEADA, "contador"),
            (T.REPORTE_DISPONIBLE, "auxiliar"),
            (T.JORNADA_SIN_CIERRE, "admin"),
            (T.EXPORTACION_FALLIDA, "admin"),
            (T.DIRECTORIO_NO_DISPONIBLE, "admin"),
        ],
    )
    def test_las_que_se_resuelven_solas_no_se_atienden_a_mano(self, tipo, usuario):
        id_ = self.n.registrar(causa(tipo, "clave:1"))
        with pytest.raises(TransicionInvalidaError, match="no se atiende a mano"):
            self.n.atender(self.u[usuario], id_)
        assert self.n.listar(self.u[usuario])[0].atendida_en is None

    def test_el_auxiliar_no_atiende_la_transaccion_en_alerta(self):
        id_ = self.n.registrar(causa(T.TRANSACCION_EN_ALERTA, "transaccion:1"))
        with pytest.raises(TransicionInvalidaError):
            self.n.atender(self.u["auxiliar"], id_)

    def test_atender_causa_solo_marca_la_abierta_de_esa_causa(self):
        self.n.registrar(causa(T.EXPORTACION_FALLIDA, "reporte:1"))
        self.n.registrar(causa(T.EXPORTACION_FALLIDA, "reporte:2"))
        assert self.n.atender_causa([T.EXPORTACION_FALLIDA, T.REPORTE_DISPONIBLE], "reporte:1", "Se exportó") == 1
        assert len(self.n.listar(self.u["admin"], no_atendidas=True)) == 1


class ContratoNotificacionesDeTransacciones:
    """
    RF04 con el repositorio de transacciones real: guardar una transacción EN_ALERTA notifica en la misma
    transacción de base de datos, y aprobarla, corregirla o rechazarla deja la notificación ATENDIDA.
    Fixtures adicionales: `transacciones` (comparte el estado con `notificaciones`), `nuevo_numero`.
    """

    @pytest.fixture(autouse=True)
    def _ayudas(self, notificaciones, usuarios, transacciones, catalogo, nuevo_numero):
        self.n, self.u, self.t = notificaciones, usuarios, transacciones
        self.catalogo, self.numero = catalogo, nuevo_numero

    def en_alerta(self, numero=None):
        return self.t.guardar(resultado_alerta_con_asiento(self.catalogo, numero or self.numero()))

    def test_una_transaccion_en_alerta_notifica_al_contador_y_al_auxiliar(self):
        id_ = self.en_alerta()
        guardada = self.t.obtener(id_)
        (contador,) = self.n.listar(self.u["contador"])
        (auxiliar,) = self.n.listar(self.u["auxiliar"])
        assert contador.id == auxiliar.id
        assert (contador.tipo, contador.transaccion_id, contador.fecha_jornada) == (
            T.TRANSACCION_EN_ALERTA,
            id_,
            guardada.fecha_jornada,
        )
        assert (contador.informativa, auxiliar.informativa) == (False, True)
        # RF04 paso 2: número de la factura, descripción de la venta y motivo de la alerta.
        assert contador.datos["numero_factura"] == guardada.numero_factura
        assert contador.datos["descripcion_venta"] == [d["descripcion"] for d in guardada.detalle_lineas]
        assert contador.datos["motivos"] == list(guardada.motivos_alerta)
        assert guardada.numero_factura in contador.mensaje and guardada.motivos_alerta[0] in contador.mensaje
        assert self.n.listar(self.u["admin"]) == []

    def test_una_transaccion_aprobada_automaticamente_no_notifica(self):
        self.t.guardar(resultado_aprobado(self.catalogo, self.numero()))
        assert self.n.listar(self.u["contador"]) == self.n.listar(self.u["auxiliar"]) == []

    def test_una_factura_duplicada_no_deja_una_segunda_notificacion(self):
        numero = self.numero()
        self.en_alerta(numero)
        with pytest.raises(FacturaDuplicadaError):
            self.en_alerta(numero)
        assert len(self.n.listar(self.u["contador"])) == 1

    @pytest.mark.parametrize("accion", ["aprobar", "corregir", "rechazar"])
    def test_resolver_la_transaccion_deja_la_notificacion_atendida_para_los_dos(self, accion):
        id_ = self.en_alerta()
        contador = self.u["contador"]
        if accion == "aprobar":
            self.t.aprobar(id_, usuario_id=contador)
        elif accion == "corregir":
            self.t.corregir(id_, partidas_correctas(self.catalogo), motivo="Corrección de prueba", usuario_id=contador)
        else:
            self.t.rechazar(id_, motivo="Rechazo de prueba del contrato", usuario_id=contador)
        verbo = {"aprobar": "aprobó", "corregir": "corrigió", "rechazar": "rechazó"}[accion]
        for usuario in ("contador", "auxiliar"):
            (vista,) = self.n.listar(self.u[usuario])
            assert vista.atendida_en is not None and vista.atendida_por is None
            assert vista.resolucion == f"La transacción se {verbo} (usuario {contador})."
        assert self.n.listar(self.u["contador"], no_atendidas=True) == []

    def test_resolver_una_no_atiende_la_de_otra_transaccion(self):
        primera, segunda = self.en_alerta(), self.en_alerta()
        self.t.aprobar(primera, usuario_id=self.u["contador"])
        (pendiente,) = self.n.listar(self.u["contador"], no_atendidas=True)
        assert pendiente.transaccion_id == segunda
