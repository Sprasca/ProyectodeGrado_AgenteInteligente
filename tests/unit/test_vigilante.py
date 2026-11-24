"""Vigilante de DIRECTORIO_ENTRADA (RF01 pasos 1-2, casos 3.a y 4.a), sin base de datos."""

import logging
import shutil
import threading
import time
import uuid

import pytest
from fabricas import xml_factura

from agente.application.recepcion import ConfiguracionRecepcion, ReceptorFacturas
from agente.infrastructure.recepcion.vigilante import VigilanteEntrada


class ObservadorFalso:
    creados: list["ObservadorFalso"] = []

    def __init__(self):
        self.programado = None
        self.activo = False
        ObservadorFalso.creados.append(self)

    def schedule(self, manejador, ruta, recursive):
        self.programado = (manejador, ruta, recursive)

    def start(self):
        self.activo = True

    def stop(self):
        self.activo = False

    def join(self, timeout=None):
        pass


class Procesador:
    def __init__(self):
        self.nombres: list[str] = []

    def __call__(self, contenido, nombre, tipo):
        self.nombres.append(nombre)
        return uuid.uuid4()


@pytest.fixture
def entorno(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    configuracion = ConfiguracionRecepcion(
        entrada=entrada,
        recibidos=entrada / "recibidos",
        no_recibidos=entrada / "no_recibidos",
        tamano_maximo=1024 * 1024,
        espera_estabilidad=0,
        reintentos=0,
        espera_inicial=0,
    )
    procesador = Procesador()
    ObservadorFalso.creados = []
    vigilante = VigilanteEntrada(ReceptorFacturas(configuracion, procesador), crear_observador=ObservadorFalso)
    return vigilante, configuracion, procesador


def _dejar(configuracion, nombre, contenido=None):
    ruta = configuracion.entrada / nombre
    ruta.write_bytes(xml_factura(nombre) if contenido is None else contenido)
    return ruta


# --- Arranque: observa la entrada y procesa lo que ya estaba (4.a) ----------------------------------------


def test_al_arrancar_observa_solo_el_primer_nivel_y_procesa_lo_pendiente(entorno, caplog):
    vigilante, configuracion, procesador = entorno
    _dejar(configuracion, "a.xml")
    _dejar(configuracion, "b.xml")
    with caplog.at_level(logging.INFO, logger="agente.recepcion"):
        vigilante.revisar()
    (observador,) = ObservadorFalso.creados
    assert observador.activo
    _, ruta, recursivo = observador.programado
    assert (ruta, recursivo) == (str(configuracion.entrada), False)
    assert "Observando el directorio de entrada" in caplog.text

    assert vigilante.atender_cola() == 2
    assert sorted(procesador.nombres) == ["a.xml", "b.xml"]
    assert sorted(p.name for p in configuracion.recibidos.iterdir()) == ["a.xml", "b.xml"]


def test_los_avisos_de_watchdog_encolan_sin_repetir(entorno):
    vigilante, configuracion, procesador = entorno
    vigilante.revisar()
    manejador = ObservadorFalso.creados[0].programado[0]
    ruta = _dejar(configuracion, "c.xml")

    class Aviso:
        def __init__(self, origen, destino=None):
            self.src_path, self.dest_path, self.is_directory = str(origen), str(destino), False

    manejador.on_created(Aviso(ruta))
    manejador.on_modified(Aviso(ruta))  # varios avisos del mismo archivo: una sola vez en cola
    assert vigilante.atender_cola() == 1
    assert procesador.nombres == ["c.xml"]

    # Renombre de un temporal al nombre final: cuenta el destino.
    temporal = configuracion.entrada / "d.tmp"
    temporal.write_bytes(xml_factura("d"))
    final = configuracion.entrada / "d.xml"
    temporal.rename(final)
    manejador.on_moved(Aviso(temporal, final))
    assert vigilante.atender_cola() == 1
    assert procesador.nombres == ["c.xml", "d.xml"]


@pytest.mark.parametrize("nombre", ["nota.txt", "f.xml.motivo.json", "sub"])
def test_ignora_lo_que_no_es_una_factura_en_la_entrada(entorno, nombre):
    vigilante, configuracion, _ = entorno
    if nombre == "sub":
        (configuracion.entrada / "sub").mkdir()
        assert not vigilante.encolar(configuracion.entrada / "sub")
    else:
        assert not vigilante.encolar(_dejar(configuracion, nombre, b"x"))


def test_no_reprocesa_recibidos_ni_no_recibidos(entorno):
    vigilante, configuracion, procesador = entorno
    configuracion.recibidos.mkdir()
    configuracion.no_recibidos.mkdir()
    (configuracion.recibidos / "ya.xml").write_bytes(xml_factura("ya"))
    (configuracion.no_recibidos / "malo.xml").write_bytes(b"<malo")
    assert not vigilante.encolar(configuracion.recibidos / "ya.xml")
    vigilante.revisar()
    assert vigilante.atender_cola() == 0 and procesador.nombres == []


def test_la_revision_periodica_recoge_lo_que_watchdog_no_aviso(entorno):
    vigilante, configuracion, procesador = entorno
    vigilante.revisar()
    _dejar(configuracion, "sin-aviso.xml")
    vigilante.revisar()
    assert vigilante.atender_cola() == 1 and procesador.nombres == ["sin-aviso.xml"]


# --- Directorio no disponible (3.a) y reanudación (4.a) -----------------------------------------------------


def test_directorio_no_disponible_alerta_una_vez_y_reanuda_al_volver(entorno, caplog):
    vigilante, configuracion, procesador = entorno
    vigilante.revisar()
    primero = ObservadorFalso.creados[0]

    shutil.rmtree(configuracion.entrada)
    with caplog.at_level(logging.INFO, logger="agente.recepcion"):
        vigilante.revisar()
        vigilante.revisar()  # sigue caído: no repite la alerta
    errores = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errores) == 1 and "no está disponible" in errores[0].getMessage()
    assert not primero.activo

    # Durante la interrupción llegaron facturas: se procesan al reanudar.
    configuracion.entrada.mkdir()
    _dejar(configuracion, "llego-en-la-caida.xml")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agente.recepcion"):
        vigilante.revisar()
    assert "volvió a estar disponible" in caplog.text
    assert ObservadorFalso.creados[-1].activo and ObservadorFalso.creados[-1] is not primero
    assert vigilante.atender_cola() == 1 and procesador.nombres == ["llego-en-la-caida.xml"]


def test_directorio_que_no_existe_al_arrancar(entorno, caplog):
    vigilante, configuracion, _ = entorno
    configuracion.entrada.rmdir()
    with caplog.at_level(logging.ERROR, logger="agente.recepcion"):
        vigilante.revisar()
    assert "no está disponible" in caplog.text
    assert ObservadorFalso.creados == []


def test_un_archivo_que_no_se_pudo_mover_no_se_reprocesa_mientras_no_cambie(entorno, monkeypatch):
    vigilante, configuracion, procesador = entorno
    import agente.application.recepcion as recepcion

    monkeypatch.setattr(recepcion.shutil, "move", lambda *_: (_ for _ in ()).throw(PermissionError("bloqueado")))
    ruta = _dejar(configuracion, "pegado.xml")
    vigilante.revisar()
    vigilante.atender_cola()
    vigilante.revisar()
    assert vigilante.atender_cola() == 0
    assert procesador.nombres == ["pegado.xml"]

    # Si el archivo cambia, se vuelve a intentar.
    time.sleep(0.01)
    ruta.write_bytes(xml_factura("pegado-v2"))
    vigilante.revisar()
    assert vigilante.atender_cola() == 1


# --- Con watchdog real (sin base de datos) ------------------------------------------------------------------


def test_watchdog_real_detecta_un_archivo_nuevo_y_lo_mueve_a_recibidos(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    configuracion = ConfiguracionRecepcion(
        entrada, entrada / "recibidos", entrada / "no_recibidos", 1024 * 1024, espera_estabilidad=0.2
    )
    procesador = Procesador()
    vigilante = VigilanteEntrada(ReceptorFacturas(configuracion, procesador), intervalo=60)  # sin revisión periódica
    detener = threading.Event()
    hilo = threading.Thread(target=vigilante.ejecutar, args=(detener,), daemon=True)
    hilo.start()
    try:
        time.sleep(0.5)  # que el observador arranque
        (entrada / "nueva.xml").write_bytes(xml_factura("FV-NUEVA"))
        limite = time.monotonic() + 15
        while not (configuracion.recibidos / "nueva.xml").exists() and time.monotonic() < limite:
            time.sleep(0.1)
    finally:
        detener.set()
        hilo.join(timeout=20)
    assert (configuracion.recibidos / "nueva.xml").exists()
    assert procesador.nombres == ["nueva.xml"]
    assert not (entrada / "nueva.xml").exists()
