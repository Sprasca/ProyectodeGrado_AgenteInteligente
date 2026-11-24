"""Reintentos de la recepción automática (RF01 1.a): solo causas temporales, con espera incremental."""

import json
import logging
import pathlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fabricas import xml_factura

from agente.application.recepcion import SUFIJO_MOTIVO, ConfiguracionRecepcion, ReceptorFacturas
from agente.config import Settings
from agente.domain.errors import FacturaDuplicadaError
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError

ESTABILIDAD = 0.25  # valor distinguible: las esperas de estabilidad no son reintentos


class Procesador:
    """Falla con `error` las primeras `fallos` veces (o siempre si fallos es None)."""

    def __init__(self, error=None, fallos=None):
        self.error, self.fallos, self.llamadas = error, fallos, 0

    def __call__(self, contenido, nombre, tipo):
        self.llamadas += 1
        if self.error is not None and (self.fallos is None or self.llamadas <= self.fallos):
            raise self.error
        return uuid.UUID(int=self.llamadas)


class Reloj:
    """dormir() falso: guarda las esperas de reintento (no las de estabilidad) y puede ejecutar algo en cada una."""

    def __init__(self, al_esperar_estabilidad=None):
        self.reintentos: list[float] = []
        self.al_esperar_estabilidad = al_esperar_estabilidad

    def __call__(self, segundos):
        if segundos == ESTABILIDAD:
            if self.al_esperar_estabilidad:
                self.al_esperar_estabilidad()
        else:
            self.reintentos.append(segundos)


@pytest.fixture
def configuracion(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    return ConfiguracionRecepcion(
        entrada=entrada,
        recibidos=entrada / "recibidos",
        no_recibidos=entrada / "no_recibidos",
        tamano_maximo=1024 * 1024,
        espera_estabilidad=ESTABILIDAD,
        reintentos=3,
        espera_inicial=1,
    )


def _recibir(configuracion, procesador, reloj=None, contenido=None, nombre="f.xml"):
    reloj = reloj or Reloj()
    ruta = configuracion.entrada / nombre
    ruta.write_bytes(xml_factura("FV-1") if contenido is None else contenido)
    receptor = ReceptorFacturas(configuracion, procesador, dormir=reloj, ahora=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    return receptor.recibir(ruta), reloj, ruta


def _motivo(configuracion, nombre="f.xml"):
    return json.loads((configuracion.no_recibidos / (nombre + SUFIJO_MOTIVO)).read_text(encoding="utf-8"))


def test_espera_incremental():
    rutas = (pathlib.Path("e"), pathlib.Path("r"), pathlib.Path("n"))
    configuracion = ConfiguracionRecepcion(*rutas, tamano_maximo=1, espera_inicial=0.5)
    assert [configuracion.espera_antes_del_reintento(i) for i in (1, 2, 3, 4)] == [0.5, 1, 2, 4]


# --- Base de datos sin respuesta ------------------------------------------------------------------------


def test_base_caida_que_se_recupera_se_recibe(configuracion, caplog):
    procesador = Procesador(RepositorioNoDisponibleError("No hay conexión con PostgreSQL."), fallos=2)
    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        recepcion, reloj, ruta = _recibir(configuracion, procesador)
    assert recepcion.recibido and recepcion.intentos == 3
    assert procesador.llamadas == 3
    assert reloj.reintentos == [1, 2]
    assert (configuracion.recibidos / "f.xml").exists() and not ruta.exists()
    assert "intento 1 de 4" in caplog.text and "Se reintenta en 1 s" in caplog.text


def test_base_caida_agota_los_reintentos_y_va_a_no_recibidos(configuracion, caplog):
    procesador = Procesador(RepositorioNoDisponibleError("No hay conexión con PostgreSQL."))
    with caplog.at_level(logging.ERROR, logger="agente.recepcion"):
        recepcion, reloj, _ = _recibir(configuracion, procesador)
    assert not recepcion.recibido and recepcion.intentos == 4
    assert procesador.llamadas == 4  # el intento inicial y 3 reintentos
    assert reloj.reintentos == [1, 2, 4]
    motivo = _motivo(configuracion)
    assert motivo["tipo_error"] == "RepositorioNoDisponibleError"
    assert motivo["intentos"] == 4
    assert "No hay conexión con PostgreSQL." in motivo["motivo"]
    assert "Se agotaron los 3 reintentos." in motivo["motivo"]
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_con_cero_reintentos_no_espera(configuracion):
    procesador = Procesador(RepositorioNoDisponibleError("caída"))
    recepcion, reloj, _ = _recibir(replace(configuracion, reintentos=0), procesador)
    assert not recepcion.recibido and recepcion.intentos == 1
    assert procesador.llamadas == 1 and reloj.reintentos == []


# --- Archivo que aún se está copiando, vacío o bloqueado -------------------------------------------------


def test_archivo_que_sigue_creciendo_se_espera_hasta_que_termina(configuracion):
    contenido = xml_factura("FV-1")
    ruta = configuracion.entrada / "f.xml"
    partes = [contenido[:100], contenido[100:400], contenido[400:]]
    ruta.write_bytes(partes[0])

    def copiar_un_poco_mas():
        # Durante las dos primeras esperas de estabilidad el archivo sigue creciendo.
        if partes[1:]:
            with ruta.open("ab") as archivo:
                archivo.write(partes.pop(1))

    reloj = Reloj(copiar_un_poco_mas)
    procesador = Procesador()
    recepcion = ReceptorFacturas(configuracion, procesador, dormir=reloj).recibir(ruta)
    assert recepcion.recibido and recepcion.intentos == 3
    assert reloj.reintentos == [1, 2]
    assert procesador.llamadas == 1  # solo se procesó cuando el archivo dejó de cambiar
    assert (configuracion.recibidos / "f.xml").read_bytes() == contenido


def test_archivo_que_nunca_termina_de_copiarse_va_a_no_recibidos(configuracion):
    ruta = configuracion.entrada / "f.xml"
    ruta.write_bytes(b"<Invoice>")

    def seguir_escribiendo():
        with ruta.open("ab") as archivo:
            archivo.write(b"x")

    procesador = Procesador()
    recepcion = ReceptorFacturas(configuracion, procesador, dormir=Reloj(seguir_escribiendo)).recibir(ruta)
    assert not recepcion.recibido and procesador.llamadas == 0
    assert "todavía se está escribiendo" in _motivo(configuracion)["motivo"]
    assert _motivo(configuracion)["tipo_error"] == "ArchivoEnEscrituraError"


def test_archivo_vacio_se_reintenta_y_luego_va_a_no_recibidos(configuracion):
    recepcion, reloj, _ = _recibir(configuracion, Procesador(), contenido=b"")
    assert not recepcion.recibido and reloj.reintentos == [1, 2, 4]
    assert "vacío" in _motivo(configuracion)["motivo"]


def test_archivo_bloqueado_que_se_libera_se_recibe(configuracion, monkeypatch):
    ruta = configuracion.entrada / "f.xml"
    ruta.write_bytes(xml_factura("FV-1"))
    abrir_original = pathlib.Path.open
    bloqueos = {"restantes": 2}

    def abrir(self, *args, **kwargs):
        if self == ruta and bloqueos["restantes"]:
            bloqueos["restantes"] -= 1
            raise PermissionError("El proceso no tiene acceso al archivo porque está siendo utilizado por otro proceso")
        return abrir_original(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", abrir)
    reloj = Reloj()
    recepcion = ReceptorFacturas(configuracion, Procesador(), dormir=reloj).recibir(ruta)
    assert recepcion.recibido and recepcion.intentos == 3
    assert reloj.reintentos == [1, 2]


# --- Fallas definitivas: no se reintentan --------------------------------------------------------------


def test_xml_invalido_no_se_reintenta(configuracion):
    procesador = Procesador()
    recepcion, reloj, _ = _recibir(configuracion, procesador, contenido=b"<no es xml")
    assert not recepcion.recibido and recepcion.intentos == 1
    assert reloj.reintentos == [] and procesador.llamadas == 0


def test_factura_duplicada_no_se_reintenta(configuracion):
    procesador = Procesador(FacturaDuplicadaError("La factura FV-1 ya fue registrada."))
    recepcion, reloj, _ = _recibir(configuracion, procesador)
    assert not recepcion.recibido and reloj.reintentos == [] and procesador.llamadas == 1


def test_error_inesperado_no_se_reintenta(configuracion):
    procesador = Procesador(ValueError("dato raro"))
    recepcion, reloj, _ = _recibir(configuracion, procesador)
    assert not recepcion.recibido and reloj.reintentos == [] and procesador.llamadas == 1


# --- Configuración -------------------------------------------------------------------------------------


def test_configuracion_por_defecto(monkeypatch, tmp_path):
    for variable in ("RECEPCION_REINTENTOS", "RECEPCION_ESPERA_INICIAL", "RECEPCION_ESPERA_ESTABILIDAD"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("DIRECTORIO_RECIBIDOS", raising=False)
    monkeypatch.delenv("DIRECTORIO_NO_RECIBIDOS", raising=False)
    monkeypatch.setenv("DIRECTORIO_ENTRADA", str(tmp_path / "entrada"))
    monkeypatch.chdir(tmp_path)  # sin .env
    configuracion = ConfiguracionRecepcion.desde_settings(Settings())
    assert (configuracion.reintentos, configuracion.espera_inicial, configuracion.espera_estabilidad) == (3, 1, 1)
    assert configuracion.recibidos == tmp_path / "entrada" / "recibidos"
    assert configuracion.no_recibidos == tmp_path / "entrada" / "no_recibidos"


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("RECEPCION_REINTENTOS", "-1"),
        ("RECEPCION_REINTENTOS", "tres"),
        ("RECEPCION_ESPERA_INICIAL", "-0.5"),
        ("RECEPCION_ESPERA_ESTABILIDAD", "un rato"),
    ],
)
def test_configuracion_invalida(monkeypatch, variable, valor):
    monkeypatch.setenv(variable, valor)
    with pytest.raises(ValueError, match=variable):
        Settings()
