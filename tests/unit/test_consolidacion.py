"""Consolidación (RF06): agrupación y partida doble en el dominio, y logs del caso de uso."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from fabricas import resultado_alerta_con_asiento, resultado_aprobado
from repositorio_memoria import RepositorioMemoria
from repositorio_notificaciones_memoria import RepositorioNotificacionesMemoria
from repositorio_reportes_memoria import RepositorioReportesMemoria

from agente.application.reportes import consolidar_reporte, estado_jornada
from agente.domain.asiento import Partida
from agente.domain.enums import Naturaleza, Rol, TipoNotificacion, TipoReporte
from agente.domain.errors import (
    ConsolidacionBloqueadaError,
    ConsolidacionInconsistenteError,
    JornadaNoTerminadaError,
    SinTransaccionesError,
    TransicionInvalidaError,
)
from agente.domain.jornada import HORARIO_INICIAL
from agente.domain.reportes import MovimientoConsolidado, SubtotalCuenta, consolidar_movimientos
from agente.domain.usuarios import Usuario
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError

D, C = Naturaleza.DEBITO, Naturaleza.CREDITO


@dataclass(frozen=True)
class T:
    numero_factura: str
    partidas: tuple[Partida, ...]
    valor_total: Decimal = Decimal("0")
    id: uuid.UUID = uuid.UUID(int=0)


def _p(cuenta, naturaleza, valor):
    return Partida(cuenta, naturaleza, Decimal(valor))


def test_agrupa_por_codigo_registrado_con_subtotales_por_cuenta_de_4_digitos():
    transacciones = [
        T("FV-1", (_p("110505", D, "119000"), _p("4135", C, "100000"), _p("240805", C, "19000")), Decimal("119000")),
        T("FV-2", (_p("110510", D, "59500"), _p("413505", C, "50000"), _p("240805", C, "9500")), Decimal("59500")),
    ]
    c = consolidar_movimientos(transacciones)
    assert c.movimientos == (
        MovimientoConsolidado("110505", "1105", Decimal("119000"), Decimal("0")),
        MovimientoConsolidado("110510", "1105", Decimal("59500"), Decimal("0")),
        MovimientoConsolidado("240805", "2408", Decimal("0"), Decimal("28500")),
        MovimientoConsolidado("4135", "4135", Decimal("0"), Decimal("100000")),
        MovimientoConsolidado("413505", "4135", Decimal("0"), Decimal("50000")),
    )
    assert c.subtotales == (
        SubtotalCuenta("1105", Decimal("178500"), Decimal("0")),
        SubtotalCuenta("2408", Decimal("0"), Decimal("28500")),
        SubtotalCuenta("4135", Decimal("0"), Decimal("150000")),
    )
    assert (c.total_debito, c.total_credito, c.valor_total, c.numero_transacciones) == (
        Decimal("178500"),
        Decimal("178500"),
        Decimal("178500"),
        2,
    )


def test_debitos_distintos_de_creditos_es_un_error_de_integridad():
    with pytest.raises(ConsolidacionInconsistenteError, match="débitos"):
        consolidar_movimientos([T("FV-1", (_p("1105", D, "100"), _p("4135", C, "90")))])


def test_una_transaccion_aprobada_sin_partidas_es_un_error_de_integridad():
    with pytest.raises(ConsolidacionInconsistenteError, match="FV-VACIA"):
        consolidar_movimientos([T("FV-VACIA", ())])


# --- Caso de uso: jornada en hora de Colombia, parciales, cierre y logs ------------------------------------

# Colombia es UTC-5. Con cierre a las 18:00, la jornada del 24 va hasta las 23:00 UTC del 24.
A_LAS_10 = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
A_LAS_17_59 = datetime(2026, 11, 24, 22, 59, tzinfo=UTC)
A_LAS_18_05 = datetime(2026, 11, 24, 23, 5, tzinfo=UTC)
A_LAS_21 = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
CORTE_24 = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)
HOY, MANANA = date(2026, 11, 24), date(2026, 11, 25)


class Reloj:
    def __init__(self, instante):
        self.instante = instante

    def __call__(self):
        return self.instante


@pytest.fixture
def repos(catalogo):
    reloj = Reloj(A_LAS_10)
    transacciones = RepositorioMemoria(catalogo, reloj=reloj)
    return transacciones, RepositorioReportesMemoria(transacciones), reloj


def _recibir(repos, catalogo, numero, instante, alerta=False):
    transacciones, _, reloj = repos
    reloj.instante = instante
    fabrica = resultado_alerta_con_asiento if alerta else resultado_aprobado
    return transacciones.guardar(fabrica(catalogo, numero))


def _consolidar(repos, tipo=TipoReporte.PARCIAL, ahora=A_LAS_10, **opciones):
    _, reportes, _ = repos
    return consolidar_reporte(reportes, HORARIO_INICIAL, usuario_id=1, tipo=tipo, ahora=ahora, **opciones)


def test_un_parcial_es_de_la_jornada_en_curso_con_corte_en_ese_momento(repos, catalogo, caplog):
    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        reporte = _consolidar(repos, ahora=A_LAS_17_59)
    assert (reporte.tipo, reporte.numero, reporte.fecha_jornada, reporte.corte) == (
        TipoReporte.PARCIAL,
        1,
        HOY,
        A_LAS_17_59,
    )
    assert "PARCIAL n.º 1 de la jornada 2026-11-24 consolidado (usuario 1)" in caplog.text


def test_un_parcial_de_otra_jornada_no_se_permite(repos, catalogo):
    with pytest.raises(TransicionInvalidaError, match="solo se hace en la jornada en curso"):
        _consolidar(repos, fecha_jornada=date(2026, 11, 23))


def test_la_factura_recibida_despues_de_la_hora_de_cierre_es_de_la_jornada_siguiente(repos, catalogo):
    antes = _recibir(repos, catalogo, "FV-ANTES", A_LAS_17_59)
    despues = _recibir(repos, catalogo, "FV-DESPUES", A_LAS_18_05)
    transacciones, _, _ = repos
    assert (transacciones.obtener(antes).fecha_jornada, transacciones.obtener(despues).fecha_jornada) == (HOY, MANANA)

    # El cierre se genera a las 21:00, pero su corte es el de las 18:00 y no incluye la de las 18:05.
    cierre = _consolidar(repos, TipoReporte.CIERRE, ahora=A_LAS_21)
    assert (cierre.tipo, cierre.fecha_jornada, cierre.corte, cierre.transacciones) == (
        TipoReporte.CIERRE,
        HOY,
        CORTE_24,
        (antes,),
    )
    # La de las 18:05 entra en un parcial de la jornada del 25, que es la que está en curso.
    parcial = _consolidar(repos, ahora=A_LAS_21)
    assert (parcial.fecha_jornada, parcial.transacciones) == (MANANA, (despues,))


def test_el_cierre_antes_de_la_hora_no_se_permite(repos, catalogo):
    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    with pytest.raises(JornadaNoTerminadaError, match="desde las 18:00"):
        _consolidar(repos, TipoReporte.CIERRE, fecha_jornada=HOY, ahora=A_LAS_17_59)


def test_el_cierre_por_defecto_es_el_de_la_ultima_jornada_terminada(repos, catalogo):
    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    # A las 17:59 del 24, la última terminada es la del 23 (sin transacciones).
    with pytest.raises(SinTransaccionesError):
        _consolidar(repos, TipoReporte.CIERRE, ahora=A_LAS_17_59)
    assert _consolidar(repos, TipoReporte.CIERRE, ahora=A_LAS_18_05).fecha_jornada == HOY


def test_alertas_pendientes_dejan_un_warning_con_el_numero(repos, catalogo, caplog):
    _recibir(repos, catalogo, "FV-ALERTA", A_LAS_10, alerta=True)
    _, reportes, _ = repos
    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(ConsolidacionBloqueadaError):
        consolidar_reporte(reportes, HORARIO_INICIAL, usuario_id=None, ahora=A_LAS_10)
    (registro,) = caplog.records
    assert registro.levelno == logging.WARNING
    assert "hay 1 transacción" in registro.getMessage() and "cierre programado" in registro.getMessage()


def test_sin_transacciones_solo_queda_en_el_log(repos, caplog):
    _, reportes, _ = repos
    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(SinTransaccionesError):
        _consolidar(repos)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert reportes.listar() == []


def test_un_descuadre_es_una_alerta_tecnica_de_nivel_error(repos, catalogo, caplog, monkeypatch):
    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    import repositorio_reportes_memoria

    def descuadrado(_):
        raise ConsolidacionInconsistenteError("Los débitos (1) no igualan a los créditos (2) del consolidado.")

    monkeypatch.setattr(repositorio_reportes_memoria, "consolidar_movimientos", descuadrado)
    with caplog.at_level(logging.INFO, logger="agente.reportes"), pytest.raises(ConsolidacionInconsistenteError):
        _consolidar(repos)
    (registro,) = caplog.records
    assert registro.levelno == logging.ERROR and "ALERTA TÉCNICA" in registro.getMessage()
    assert repos[1].listar() == []


# --- Estado de la jornada (GET /reportes/jornada) ---------------------------------------------------------


def _estado(repos, ahora, fecha=None):
    return estado_jornada(repos[1], HORARIO_INICIAL, fecha_jornada=fecha, ahora=ahora)


def test_estado_de_la_jornada_en_curso(repos, catalogo):
    estado = _estado(repos, A_LAS_10)
    assert (estado.fecha_jornada, estado.en_curso, estado.terminada) == (HOY, True, False)
    assert (estado.hora_apertura, estado.hora_cierre, estado.corte_cierre) == (time(8, 0), time(18, 0), CORTE_24)
    assert (estado.reportes_vigentes, estado.maximo_reportes, estado.tiene_cierre) == (0, 5, False)
    assert not estado.puede_consolidar and "No hay transacciones aprobadas" in " ".join(estado.motivos)

    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    estado = _estado(repos, A_LAS_10)
    assert (estado.puede_consolidar_parcial, estado.puede_cerrar, estado.puede_consolidar) == (True, False, True)
    assert "El cierre se hace desde las 18:00 (hora de Colombia)." in estado.motivos


def test_estado_con_alertas_deshabilita_todo(repos, catalogo):
    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    _recibir(repos, catalogo, "FV-A", A_LAS_10, alerta=True)
    estado = _estado(repos, A_LAS_18_05, HOY)
    assert (estado.terminada, estado.alertas_pendientes, estado.puede_consolidar) == (True, 1, False)
    assert "Hay 1 transacción(es) en alerta" in " ".join(estado.motivos)


def test_estado_tras_el_cierre(repos, catalogo):
    _recibir(repos, catalogo, "FV-1", A_LAS_10)
    _consolidar(repos)
    _recibir(repos, catalogo, "FV-2", A_LAS_17_59)
    estado = _estado(repos, A_LAS_18_05, HOY)
    assert (estado.en_curso, estado.puede_consolidar_parcial, estado.puede_cerrar) == (False, False, True)
    cierre = _consolidar(repos, TipoReporte.CIERRE, ahora=A_LAS_18_05)
    estado = _estado(repos, A_LAS_18_05, HOY)
    assert (estado.tiene_cierre, estado.cierre_id, estado.reportes_vigentes, estado.parciales_vigentes) == (
        True,
        cierre.id,
        2,
        1,
    )
    assert not estado.puede_consolidar and "ya tiene reporte de cierre" in estado.motivos[0]


def test_un_descuadre_notifica_al_admin_y_se_atiende_a_mano(repos, catalogo, monkeypatch):
    import repositorio_reportes_memoria

    admin = Usuario(3, "admin", "Admin", Rol.ADMIN)
    notificaciones = RepositorioNotificacionesMemoria(lambda: [admin])
    _recibir(repos, catalogo, "FV-1", A_LAS_10)

    def descuadrado(_):
        raise ConsolidacionInconsistenteError("Los débitos (1) no igualan a los créditos (2) del consolidado.")

    monkeypatch.setattr(repositorio_reportes_memoria, "consolidar_movimientos", descuadrado)
    with pytest.raises(ConsolidacionInconsistenteError):
        _consolidar(repos, notificaciones=notificaciones)
    (notificacion,) = notificaciones.listar(admin.id)
    assert (notificacion.tipo, notificacion.fecha_jornada) == (TipoNotificacion.DESCUADRE, HOY)
    assert "Los débitos (1) no igualan" in notificacion.mensaje and notificacion.se_atiende_a_mano
    assert notificaciones.atender(admin.id, notificacion.id).atendida_por == admin.id


def test_si_no_se_puede_registrar_la_notificacion_el_error_original_sigue(repos, catalogo, caplog):
    class SinBase:
        def registrar(self, causa):
            raise RepositorioNoDisponibleError("sin conexión")

    _recibir(repos, catalogo, "FV-ALERTA", A_LAS_10, alerta=True)
    with caplog.at_level(logging.ERROR, logger="agente.reportes"), pytest.raises(ConsolidacionBloqueadaError):
        _consolidar(repos, notificaciones=SinBase())
    assert "no se pudo registrar la notificación CONSOLIDACION_BLOQUEADA" in caplog.text

