"""Pruebas de contrato de RepositorioNotificaciones (RF04).

Las heredan test_repositorio_notificaciones_memoria.py (unitarias) y test_repositorio_notificaciones_sql.py
(integración, sobre una base temporal: los destinatarios son TODOS los usuarios activos de cada rol).
Cada módulo define los fixtures:
- `notificaciones`: el repositorio;
- `usuarios`: {"contador", "auxiliar", "admin", "admin_inactivo"} → id, ya creados.
ContratoNotificacionesDeTransacciones pide además `transacciones` y `nuevo_numero`;
ContratoNotificacionesDeReportes, además, `reportes`.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fabricas import partidas_correctas, resultado_alerta_con_asiento, resultado_aprobado

from agente.application.notificaciones import DespachadorCorreos
from agente.application.reportes import consolidar_reporte, exportar_reporte
from agente.domain.enums import ClaseNotificacion, EstadoEnvio, TipoNotificacion, TipoReporte
from agente.domain.errors import (
    ConsolidacionBloqueadaError,
    ExportacionFallidaError,
    FacturaDuplicadaError,
    NotificacionNoEncontradaError,
    TransicionInvalidaError,
)
from agente.domain.jornada import HORARIO_INICIAL
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


LEJANO = datetime(2099, 1, 1, tzinfo=UTC)  # todas las jornadas de las pruebas ya terminaron


class ContratoNotificacionesDeReportes:
    """
    RF06 y RF07 con los repositorios reales: reporte disponible (contador y auxiliar), consolidación bloqueada
    (contador) y exportación fallida (ADMIN), y su atención automática. Fixtures adicionales: `transacciones`,
    `reportes` (comparten el estado con `notificaciones`) y `nuevo_numero`.
    """

    @pytest.fixture(autouse=True)
    def _ayudas(self, notificaciones, usuarios, transacciones, reportes, catalogo, nuevo_numero):
        self.n, self.u, self.t, self.r = notificaciones, usuarios, transacciones, reportes
        self.catalogo, self.numero = catalogo, nuevo_numero

    def aprobada(self):
        return self.t.guardar(resultado_aprobado(self.catalogo, self.numero()))

    def en_alerta(self):
        return self.t.guardar(resultado_alerta_con_asiento(self.catalogo, self.numero()))

    def cerrar(self, jornada):
        return consolidar_reporte(
            self.r,
            HORARIO_INICIAL,
            usuario_id=self.u["auxiliar"],
            tipo=TipoReporte.CIERRE,
            fecha_jornada=jornada,
            ahora=LEJANO,
            notificaciones=self.n,
        )

    def de_tipo(self, usuario, tipo):
        return [v for v in self.n.listar(self.u[usuario]) if v.tipo is tipo]

    def test_reporte_disponible_para_el_contador_y_el_auxiliar(self):
        jornada = self.t.obtener(self.aprobada()).fecha_jornada
        reporte = self.cerrar(jornada)
        (contador,) = self.de_tipo("contador", T.REPORTE_DISPONIBLE)
        (auxiliar,) = self.de_tipo("auxiliar", T.REPORTE_DISPONIBLE)
        assert contador.id == auxiliar.id and contador.reporte_id == reporte.id
        assert (contador.informativa, auxiliar.informativa) == (False, False)
        assert contador.datos == {
            "tipo_reporte": "CIERRE",
            "numero": 1,
            "numero_transacciones": 1,
            "valor_total": str(reporte.valor_total),
        }
        assert self.de_tipo("admin", T.REPORTE_DISPONIBLE) == []

    @pytest.mark.parametrize("accion", ["exportar", "anular"])
    def test_exportar_o_anular_atiende_el_reporte_disponible(self, accion):
        jornada = self.t.obtener(self.aprobada()).fecha_jornada
        reporte = self.cerrar(jornada)
        contador = self.u["contador"]
        if accion == "exportar":
            self.r.exportar(reporte.id, usuario_id=contador, generar=lambda *_: b"<x/>")
        else:
            self.r.anular(reporte.id, motivo="Anulación de prueba del contrato", usuario_id=contador)
        estado = "EXPORTADO" if accion == "exportar" else "ANULADO"
        for usuario in ("contador", "auxiliar"):
            (vista,) = self.de_tipo(usuario, T.REPORTE_DISPONIBLE)
            assert vista.resolucion == f"El reporte quedó {estado} (usuario {contador})."

    def test_consolidacion_bloqueada_para_el_contador_y_atendida_al_resolver_la_ultima_alerta(self):
        primera, segunda = self.en_alerta(), self.en_alerta()
        jornada = self.t.obtener(primera).fecha_jornada
        with pytest.raises(ConsolidacionBloqueadaError):
            self.cerrar(jornada)
        (bloqueo,) = self.de_tipo("contador", T.CONSOLIDACION_BLOQUEADA)
        assert (bloqueo.fecha_jornada, bloqueo.datos["pendientes"]) == (jornada, 2)
        assert self.de_tipo("auxiliar", T.CONSOLIDACION_BLOQUEADA) == []
        assert self.de_tipo("admin", T.CONSOLIDACION_BLOQUEADA) == []

        self.t.aprobar(primera, usuario_id=self.u["contador"])
        with pytest.raises(ConsolidacionBloqueadaError):
            self.cerrar(jornada)  # el mismo bloqueo, actualizado: no una segunda notificación
        (bloqueo,) = self.de_tipo("contador", T.CONSOLIDACION_BLOQUEADA)
        assert bloqueo.datos["pendientes"] == 1 and bloqueo.atendida_en is None

        self.t.rechazar(segunda, motivo="Rechazo de prueba del contrato", usuario_id=self.u["contador"])
        (bloqueo,) = self.de_tipo("contador", T.CONSOLIDACION_BLOQUEADA)
        assert bloqueo.atendida_en is not None
        assert bloqueo.resolucion == "Ya no quedan transacciones en alerta en la jornada."

    def test_exportacion_fallida_para_el_admin_y_atendida_al_exportar(self):
        jornada = self.t.obtener(self.aprobada()).fecha_jornada
        reporte = self.cerrar(jornada)

        def falla(*_):
            raise OSError("disco lleno")

        with pytest.raises(ExportacionFallidaError):
            exportar_reporte(self.r, reporte.id, usuario_id=self.u["contador"], generar=falla, notificaciones=self.n)
        (fallida,) = self.de_tipo("admin", T.EXPORTACION_FALLIDA)
        assert fallida.reporte_id == reporte.id and "disco lleno" in fallida.mensaje
        assert fallida.se_atiende_a_mano is False
        assert self.de_tipo("contador", T.EXPORTACION_FALLIDA) == []

        exportar_reporte(
            self.r, reporte.id, usuario_id=self.u["contador"], generar=lambda *_: b"<x/>", notificaciones=self.n
        )
        (fallida,) = self.de_tipo("admin", T.EXPORTACION_FALLIDA)
        assert fallida.atendida_en is not None


class ContratoEnviosCorreo:
    """
    El correo (canal opcional) con SMTP configurado: un envío PENDIENTE por destinatario con email, que el
    despachador envía con hasta 3 intentos y espera creciente; si fallan, FALLIDA y CORREO_NO_ENVIADO al ADMIN.
    Fixtures: `notificaciones` con el correo activo y `usuarios` donde el contador y el ADMIN tienen email y
    el auxiliar no.
    """

    @pytest.fixture(autouse=True)
    def _ayudas(self, notificaciones, usuarios):
        self.n, self.u = notificaciones, usuarios
        self.enviados = []
        self.reloj = [datetime.now(UTC)]  # los envíos se crean con la hora real

    def alerta(self):
        return self.n.registrar(causa(T.TRANSACCION_EN_ALERTA, "transaccion:1", "FV-1 en alerta"))

    def despachador(self, falla=None):
        def enviar(destinatario, asunto, cuerpo):
            if falla is not None:
                raise falla
            self.enviados.append((destinatario, asunto, cuerpo))

        return DespachadorCorreos(self.n, enviar, intentos_maximos=3, espera_inicial=60, ahora=lambda: self.reloj[0])

    def avanzar(self, segundos):
        self.reloj[0] += timedelta(seconds=segundos)

    def test_un_envio_pendiente_solo_para_quien_tiene_correo(self):
        self.alerta()
        (contador,) = self.n.listar(self.u["contador"])
        (auxiliar,) = self.n.listar(self.u["auxiliar"])
        assert (contador.correo, auxiliar.correo) == (EstadoEnvio.PENDIENTE, None)
        (pendiente,) = self.n.envios_pendientes(self.reloj[0] + timedelta(minutes=1))
        assert (pendiente.destinatario, pendiente.tipo, pendiente.intentos) == (
            "contador@empresa.co",
            T.TRANSACCION_EN_ALERTA,
            0,
        )

    def test_el_despachador_envia_y_queda_enviada(self):
        self.alerta()
        self.avanzar(1)
        assert self.despachador().revisar() == 1
        ((destinatario, asunto, cuerpo),) = self.enviados
        assert destinatario == "contador@empresa.co"
        assert asunto == "[Agente PUC] Transacción en alerta" and "FV-1 en alerta" in cuerpo
        assert self.n.listar(self.u["contador"])[0].correo is EstadoEnvio.ENVIADA
        assert self.n.envios_pendientes(self.reloj[0] + timedelta(days=1)) == []

    def test_tres_intentos_con_espera_creciente_y_luego_fallida_con_aviso_al_admin(self):
        self.alerta()
        despachador = self.despachador(OSError("SMTP caído"))
        self.avanzar(1)
        assert despachador.revisar() == 0  # intento 1
        self.avanzar(59)
        assert self.n.envios_pendientes(self.reloj[0]) == []  # aún no le toca (espera 60 s)
        self.avanzar(1)
        despachador.revisar()  # intento 2
        self.avanzar(119)
        assert self.n.envios_pendientes(self.reloj[0]) == []  # ahora espera 120 s
        self.avanzar(1)
        (envio,) = self.n.envios_pendientes(self.reloj[0])
        assert envio.intentos == 2
        despachador.revisar()  # intento 3: FALLIDA
        assert self.n.envios_pendientes(self.reloj[0] + timedelta(days=1)) == []

        (contador,) = self.n.listar(self.u["contador"])
        assert contador.correo is EstadoEnvio.FALLIDA and contador.atendida_en is None  # sigue en la bandeja
        (aviso,) = [v for v in self.n.listar(self.u["admin"]) if v.tipo is T.CORREO_NO_ENVIADO]
        assert "contador@empresa.co" in aviso.mensaje and "SMTP caído" in aviso.mensaje
        assert aviso.datos["intentos"] == 3 and aviso.se_atiende_a_mano
        assert aviso.correo is None  # el aviso de un correo fallido no se envía por correo

    def test_una_tecnica_al_admin_con_correo(self):
        self.n.registrar(causa())
        (pendiente,) = self.n.envios_pendientes(self.reloj[0] + timedelta(minutes=1))
        assert pendiente.destinatario == "admin@empresa.co"

