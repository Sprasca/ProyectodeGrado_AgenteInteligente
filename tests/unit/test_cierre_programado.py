"""Cierre diario programado (RF06): un solo intento a la HORA_CIERRE de Colombia, sin usuario y sin reintentos.

Si la jornada queda sin reporte de cierre, se registra la alerta técnica JORNADA_SIN_CIERRE.
"""

import importlib.util
import logging
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from fabricas import resultado_alerta_con_asiento, resultado_aprobado
from repositorio_jornada_memoria import RepositorioAlertasMemoria, RepositorioHorarioMemoria
from repositorio_memoria import RepositorioMemoria
from repositorio_reportes_memoria import RepositorioReportesMemoria

from agente.application.cierre import CierreProgramado
from agente.domain.enums import TipoAlertaTecnica, TipoReporte
from agente.domain.errors import ConsolidacionInconsistenteError
from agente.domain.jornada import Horario
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError
from agente.infrastructure.recepcion.vigilante import VigilanteEntrada

# 24 de noviembre de 2026 en Colombia (UTC-5): las 18:00 son las 23:00 UTC.
CORTE_24 = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)
A_LAS_10 = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
HOY, AYER = date(2026, 11, 24), date(2026, 11, 23)


class Reloj:
    def __init__(self, instante):
        self.instante = instante

    def __call__(self):
        return self.instante


class Entorno:
    def __init__(self, catalogo, instante):
        self.reloj = Reloj(instante)
        self.horario = RepositorioHorarioMemoria()
        self.transacciones = RepositorioMemoria(catalogo, horario=self.horario, reloj=self.reloj)
        self.reportes = RepositorioReportesMemoria(self.transacciones)
        self.alertas = RepositorioAlertasMemoria()
        self.catalogo = catalogo
        self.cierre = CierreProgramado(self.reportes, self.horario, self.alertas, ahora=self.reloj)

    def recibir(self, numero, alerta=False):
        fabrica = resultado_alerta_con_asiento if alerta else resultado_aprobado
        return self.transacciones.guardar(fabrica(self.catalogo, numero))

    def cierres(self):
        return [r for r in self.reportes.listar() if r.tipo is TipoReporte.CIERRE]


@pytest.fixture
def entorno(catalogo):
    return Entorno(catalogo, A_LAS_10)


def test_antes_de_la_hora_no_cierra_la_jornada_en_curso(entorno):
    entorno.recibir("FV-1")
    entorno.reloj.instante = CORTE_24 - timedelta(minutes=1)
    entorno.cierre.revisar()  # al arrancar revisa la jornada anterior (la del 23, sin transacciones)
    assert entorno.cierres() == []
    assert entorno.cierre.revisar() is False


def test_a_la_hora_cierra_una_sola_vez_sin_usuario_y_con_corte_a_esa_hora(entorno):
    id_ = entorno.recibir("FV-1")
    entorno.reloj.instante = CORTE_24
    assert entorno.cierre.revisar() is True
    entorno.reloj.instante += timedelta(hours=2)  # 20:00 en Colombia: mismo intento ya hecho
    assert entorno.cierre.revisar() is False
    (cierre,) = entorno.cierres()
    assert (cierre.fecha_jornada, cierre.corte, cierre.transacciones) == (HOY, CORTE_24, (id_,))
    assert entorno.reportes.eventos(cierre.id)[0].usuario_id is None
    assert entorno.alertas.listar() == []


def test_si_el_proceso_arranca_despues_de_la_hora_hace_el_mismo_intento_con_el_corte_original(entorno):
    id_ = entorno.recibir("FV-1")
    entorno.reloj.instante = CORTE_24 + timedelta(hours=3, minutes=30)  # 21:30
    tardia = entorno.recibir("FV-TARDIA")  # llegó después de las 18:00: es de la jornada del 25
    assert entorno.cierre.revisar() is True
    (cierre,) = entorno.cierres()
    assert (cierre.fecha_jornada, cierre.corte, cierre.transacciones) == (HOY, CORTE_24, (id_,))
    assert entorno.transacciones.obtener(tardia).reporte_id is None


def test_al_dia_siguiente_cierra_la_nueva_jornada(entorno):
    entorno.recibir("FV-1")
    entorno.reloj.instante = CORTE_24
    entorno.cierre.revisar()
    entorno.reloj.instante = CORTE_24 + timedelta(hours=12)  # 06:00 del 25
    entorno.recibir("FV-2")
    assert entorno.cierre.revisar() is False
    entorno.reloj.instante = CORTE_24 + timedelta(days=1)
    assert entorno.cierre.revisar() is True
    assert sorted(c.fecha_jornada for c in entorno.cierres()) == [HOY, HOY + timedelta(days=1)]


def test_usa_la_hora_de_cierre_guardada_en_la_base(entorno):
    entorno.recibir("FV-1")
    entorno.horario.cambiar(time(7, 0), time(17, 0), usuario_id=1, ahora=A_LAS_10)
    entorno.reloj.instante = CORTE_24 - timedelta(hours=1)  # 17:00
    assert entorno.cierre.revisar() is True
    (cierre,) = entorno.cierres()
    assert cierre.corte == CORTE_24 - timedelta(hours=1)


# --- Sin reintentos: alerta técnica JORNADA_SIN_CIERRE ------------------------------------------------------


def test_con_alertas_no_cierra_no_reintenta_y_registra_la_alerta_tecnica(entorno, caplog):
    entorno.recibir("FV-1")
    entorno.recibir("FV-A1", alerta=True)
    entorno.recibir("FV-A2", alerta=True)
    entorno.reloj.instante = CORTE_24
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        assert entorno.cierre.revisar() is True
    assert entorno.cierres() == []
    (alerta,) = entorno.alertas.listar()
    assert (alerta.tipo, alerta.fecha_jornada) == (TipoAlertaTecnica.JORNADA_SIN_CIERRE, HOY)
    assert "la jornada no tiene reporte de cierre" in alerta.mensaje
    assert alerta.datos["alertas_pendientes"] == 2
    errores = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errores) == 1 and "ALERTA TÉCNICA" in errores[0].getMessage()

    # Aunque el contador resuelva las alertas, el proceso no vuelve a intentar: el cierre se hace a mano.
    for t in entorno.transacciones.listar():
        if t.motivos_alerta:
            entorno.transacciones.aprobar(t.id, usuario_id=1)
    entorno.reloj.instante += timedelta(hours=1)
    assert entorno.cierre.revisar() is False
    assert entorno.cierres() == []


def test_si_todas_las_transacciones_estan_en_alerta_registra_la_alerta_con_las_pendientes(entorno):
    """Sin ninguna aprobada sí hay trabajo pendiente para el contador: la jornada queda sin cierre y se alerta."""
    entorno.recibir("FV-A1", alerta=True)
    entorno.recibir("FV-A2", alerta=True)
    entorno.recibir("FV-A3", alerta=True)
    entorno.reloj.instante = CORTE_24
    assert entorno.cierre.revisar() is True
    assert entorno.cierres() == []
    (alerta,) = entorno.alertas.listar()
    assert (alerta.tipo, alerta.fecha_jornada) == (TipoAlertaTecnica.JORNADA_SIN_CIERRE, HOY)
    assert alerta.datos["alertas_pendientes"] == 3
    assert "Transacciones en alerta pendientes: 3" in alerta.mensaje


def test_si_todas_las_transacciones_fueron_rechazadas_no_hay_alerta(entorno):
    """Solo una jornada sin transacciones (sin contar las RECHAZADAS) se cierra sin reporte y sin alerta."""
    for numero in ("FV-R1", "FV-R2"):
        entorno.transacciones.rechazar(entorno.recibir(numero), motivo="Factura de prueba anulada", usuario_id=1)
    entorno.reloj.instante = CORTE_24
    assert entorno.cierre.revisar() is True
    assert entorno.reportes.listar() == [] and entorno.alertas.listar() == []


def test_al_reiniciar_no_repite_el_intento_que_ya_fallo(entorno):
    entorno.recibir("FV-1")
    entorno.recibir("FV-A", alerta=True)
    entorno.reloj.instante = CORTE_24
    entorno.cierre.revisar()
    for t in entorno.transacciones.listar():
        if t.motivos_alerta:
            entorno.transacciones.aprobar(t.id, usuario_id=1)
    reiniciado = CierreProgramado(entorno.reportes, entorno.horario, entorno.alertas, ahora=entorno.reloj)
    assert reiniciado.revisar() is True  # revisa, pero la alerta registrada indica que ya se intentó
    assert entorno.cierres() == []
    assert len(entorno.alertas.listar()) == 1


def test_al_reiniciar_si_ya_se_cerro_a_mano_no_hace_nada(entorno):
    entorno.recibir("FV-1")
    entorno.reloj.instante = CORTE_24 + timedelta(minutes=5)
    entorno.reportes.consolidar(fecha_jornada=HOY, tipo=TipoReporte.CIERRE, corte=CORTE_24, usuario_id=1)
    entorno.cierre.revisar()
    assert len(entorno.cierres()) == 1 and entorno.alertas.listar() == []


def test_un_descuadre_deja_la_alerta_tecnica(entorno, monkeypatch, caplog):
    entorno.recibir("FV-1")
    import repositorio_reportes_memoria

    def descuadrado(_):
        raise ConsolidacionInconsistenteError("Los débitos no igualan a los créditos.")

    monkeypatch.setattr(repositorio_reportes_memoria, "consolidar_movimientos", descuadrado)
    entorno.reloj.instante = CORTE_24
    with caplog.at_level(logging.ERROR, logger="agente.reportes"):
        assert entorno.cierre.revisar() is True
    (alerta,) = entorno.alertas.listar()
    assert "Los débitos no igualan" in alerta.datos["causa"] and alerta.datos["alertas_pendientes"] == 0


def test_sin_transacciones_no_crea_reporte_ni_alerta(entorno, caplog):
    entorno.reloj.instante = CORTE_24
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        assert entorno.cierre.revisar() is True
    assert entorno.reportes.listar() == [] and entorno.alertas.listar() == []
    assert "nada que cerrar" in caplog.text


def test_si_la_base_no_responde_no_reintenta(entorno, caplog):
    class SinBase:
        def resumen_jornada(self, _):
            raise RepositorioNoDisponibleError("sin conexión")

    entorno.cierre.reportes = SinBase()
    entorno.reloj.instante = CORTE_24
    with caplog.at_level(logging.ERROR, logger="agente.reportes"):
        assert entorno.cierre.revisar() is True
        assert entorno.cierre.revisar() is False
    assert "No se reintenta" in caplog.text and "la jornada no tiene reporte de cierre" in caplog.text


def test_si_no_puede_leer_el_horario_lo_registra_y_sigue(entorno, caplog):
    class SinHorario:
        def vigente(self) -> Horario:
            raise RepositorioNoDisponibleError("sin conexión")

    entorno.cierre.horarios = SinHorario()
    with caplog.at_level(logging.ERROR, logger="agente.reportes"):
        assert entorno.cierre.revisar() is False
    assert "no se pudo leer el horario" in caplog.text


# --- El vigilante ejecuta el cierre en cada revisión ---------------------------------------------------------


def test_el_vigilante_ejecuta_las_tareas_y_sobrevive_a_un_error(tmp_path, caplog):
    llamadas = []

    def falla():
        raise RuntimeError("tarea rota")

    vigilante = VigilanteEntrada(receptor=None, tareas=[falla, lambda: llamadas.append(1)])
    with caplog.at_level(logging.ERROR):
        vigilante.ejecutar_tareas()
        vigilante.ejecutar_tareas()
    assert llamadas == [1, 1]
    assert "Falló una tarea periódica" in caplog.text


def test_una_vez_no_ejecuta_el_cierre(tmp_path, monkeypatch):
    """--una-vez solo procesa lo pendiente en la carpeta: nunca consolida."""
    from agente.config import get_settings

    ruta = Path(__file__).resolve().parents[2] / "scripts" / "recibir_facturas.py"
    spec = importlib.util.spec_from_file_location("recibir_facturas_cierre", ruta)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setenv("DIRECTORIO_ENTRADA", str(tmp_path))
    get_settings.cache_clear()

    def no_debe_llamarse():
        raise AssertionError("--una-vez no debe crear el cierre programado")

    monkeypatch.setattr(script, "cierre_por_defecto", no_debe_llamarse)
    try:
        assert script.main(["--una-vez", "--sin-nlp"]) == 0
    finally:
        get_settings.cache_clear()
