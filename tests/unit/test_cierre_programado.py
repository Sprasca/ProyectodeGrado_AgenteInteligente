"""Cierre diario programado (RF06): a la HORA_CIERRE de Colombia, un intento por jornada, sin usuario."""

import importlib.util
import logging
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from fabricas import resultado_alerta_con_asiento, resultado_aprobado
from repositorio_memoria import RepositorioMemoria
from repositorio_reportes_memoria import RepositorioReportesMemoria

from agente.application.cierre import CierreProgramado
from agente.application.reportes import consolidar_reporte
from agente.config import get_settings
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError
from agente.infrastructure.recepcion.vigilante import VigilanteEntrada

# 24 de noviembre de 2026 en Colombia (UTC-5): las 18:00 son las 23:00 UTC.
CORTE_24 = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)


class Reloj:
    def __init__(self, instante):
        self.instante = instante

    def __call__(self):
        return self.instante


class Consolidador:
    def __init__(self, error=None):
        self.cortes = []
        self.error = error

    def __call__(self, corte):
        self.cortes.append(corte)
        if self.error is not None:
            raise self.error


def _cierre(instante, consolidar=None):
    reloj = Reloj(instante)
    consolidar = consolidar or Consolidador()
    return CierreProgramado(time(18, 0), consolidar, ahora=reloj), reloj, consolidar


def test_antes_de_la_hora_no_consolida():
    cierre, _, consolidar = _cierre(CORTE_24 - timedelta(minutes=1))
    assert cierre.revisar() is False and consolidar.cortes == []


def test_a_la_hora_consolida_una_sola_vez_por_jornada_con_corte_a_esa_hora():
    cierre, reloj, consolidar = _cierre(CORTE_24)
    assert cierre.revisar() is True
    reloj.instante += timedelta(hours=2)  # 20:00 en Colombia, misma jornada
    assert cierre.revisar() is False
    assert consolidar.cortes == [CORTE_24]


def test_al_dia_siguiente_vuelve_a_cerrar():
    cierre, reloj, consolidar = _cierre(CORTE_24)
    cierre.revisar()
    reloj.instante = CORTE_24 + timedelta(hours=6)  # 00:00 del 25 en Colombia: aún no es la hora
    assert cierre.revisar() is False
    reloj.instante = CORTE_24 + timedelta(days=1)
    assert cierre.revisar() is True
    assert consolidar.cortes == [CORTE_24, CORTE_24 + timedelta(days=1)]


def test_si_el_proceso_arranca_despues_de_la_hora_hace_el_cierre_pendiente_con_corte_a_las_18():
    cierre, _, consolidar = _cierre(CORTE_24 + timedelta(hours=3, minutes=30))  # 21:30 en Colombia
    assert cierre.revisar() is True
    assert consolidar.cortes == [CORTE_24]  # lo recibido después de las 18:00 queda para mañana


def test_el_dia_se_corta_en_colombia_no_en_utc():
    # 02:00 UTC del 25 = 21:00 del 24 en Colombia: el cierre pendiente es el del 24, no el del 25.
    cierre, _, consolidar = _cierre(datetime(2026, 11, 25, 2, 0, tzinfo=UTC))
    cierre.revisar()
    assert consolidar.cortes == [CORTE_24]


def test_si_la_base_no_responde_se_reintenta_en_la_siguiente_revision(caplog):
    consolidar = Consolidador(RepositorioNoDisponibleError("sin conexión"))
    cierre, _, _ = _cierre(CORTE_24, consolidar)
    with caplog.at_level(logging.ERROR, logger="agente.reportes"):
        cierre.revisar()
        consolidar.error = None
        cierre.revisar()
    assert consolidar.cortes == [CORTE_24, CORTE_24]
    assert "Se reintentará" in caplog.text


def test_un_error_inesperado_queda_en_el_log_y_no_detiene_el_proceso(caplog):
    cierre, _, _ = _cierre(CORTE_24, Consolidador(RuntimeError("inesperado")))
    with caplog.at_level(logging.ERROR, logger="agente.reportes"):
        assert cierre.revisar() is True
    assert "ALERTA TÉCNICA" in caplog.text


# --- Con el caso de uso real (repositorios en memoria) -----------------------------------------------------


@pytest.fixture
def repos(catalogo):
    transacciones = RepositorioMemoria(catalogo)
    return transacciones, RepositorioReportesMemoria(transacciones)


def _cierre_real(reportes, instante):
    return CierreProgramado(
        time(18, 0), lambda corte: consolidar_reporte(reportes, usuario_id=None, corte=corte), ahora=Reloj(instante)
    )


def test_el_cierre_consolida_sin_usuario(repos, catalogo):
    transacciones, reportes = repos
    transacciones.guardar(resultado_aprobado(catalogo, "FV-1"))
    ahora = datetime.now(UTC) + timedelta(days=1)  # después de la recepción, y ya pasada la hora de cierre
    _cierre_real(reportes, ahora.replace(hour=23, minute=30)).revisar()
    (reporte,) = reportes.listar()
    assert reportes.eventos(reporte.id)[0].usuario_id is None


def test_con_alertas_no_consolida_y_lo_registra_en_el_log(repos, catalogo, caplog):
    transacciones, reportes = repos
    transacciones.guardar(resultado_aprobado(catalogo, "FV-1"))
    transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-ALERTA"))
    ahora = (datetime.now(UTC) + timedelta(days=1)).replace(hour=23, minute=30)
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        assert _cierre_real(reportes, ahora).revisar() is True
    assert reportes.listar() == []
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "hay 1 transacción" in avisos[0].getMessage()


def test_sin_transacciones_no_crea_reporte_vacio(repos, caplog):
    _, reportes = repos
    with caplog.at_level(logging.INFO, logger="agente.reportes"):
        _cierre_real(reportes, CORTE_24).revisar()
    assert reportes.listar() == []
    assert "no se crea un reporte" in caplog.text


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
    ruta = Path(__file__).resolve().parents[2] / "scripts" / "recibir_facturas.py"
    spec = importlib.util.spec_from_file_location("recibir_facturas_cierre", ruta)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setenv("DIRECTORIO_ENTRADA", str(tmp_path))
    get_settings.cache_clear()

    def no_debe_llamarse(_):
        raise AssertionError("--una-vez no debe crear el cierre programado")

    monkeypatch.setattr(script, "cierre_por_defecto", no_debe_llamarse)
    try:
        assert script.main(["--una-vez", "--sin-nlp"]) == 0
    finally:
        get_settings.cache_clear()
