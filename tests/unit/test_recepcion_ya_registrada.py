"""Caso límite del duplicado: una factura que la recepción automática ya registró pero no alcanzó a mover."""

import hashlib
import json
import logging
import uuid

import pytest
from fabricas import xml_factura

from agente.application.recepcion import SUFIJO_MOTIVO, ConfiguracionRecepcion, ReceptorFacturas
from agente.domain.documento import DocumentoRegistrado
from agente.domain.enums import CanalRecepcion
from agente.domain.errors import FacturaDuplicadaError

EXISTENTE = uuid.UUID(int=42)


class ProcesadorQueRechazaDuplicados:
    """Como el repositorio: un SHA-256 ya registrado es FacturaDuplicadaError."""

    def __init__(self):
        self.llamadas = 0

    def __call__(self, contenido, nombre, tipo):
        self.llamadas += 1
        sha256 = hashlib.sha256(contenido).hexdigest()
        raise FacturaDuplicadaError(f"El documento con SHA-256 {sha256} ya fue registrado en {EXISTENTE}.")


@pytest.fixture
def configuracion(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    return ConfiguracionRecepcion(
        entrada, entrada / "recibidos", entrada / "no_recibidos", 1024 * 1024, espera_estabilidad=0
    )


def _receptor(configuracion, registro, procesador):
    consultas = []

    def consultar(sha256):
        consultas.append(sha256)
        return registro

    receptor = ReceptorFacturas(configuracion, procesador, dormir=lambda _: None, consultar_registro=consultar)
    return receptor, consultas


def test_ya_registrada_por_la_carpeta_con_el_mismo_nombre_va_a_recibidos_sin_transaccion_nueva(configuracion, caplog):
    contenido = xml_factura("FV-YA")
    ruta = configuracion.entrada / "factura.xml"
    ruta.write_bytes(contenido)
    procesador = ProcesadorQueRechazaDuplicados()
    registro = DocumentoRegistrado(EXISTENTE, "factura.xml", CanalRecepcion.CARPETA)
    receptor, consultas = _receptor(configuracion, registro, procesador)

    with caplog.at_level(logging.INFO, logger="agente.recepcion"):
        recepcion = receptor.recibir(ruta)

    assert consultas == [hashlib.sha256(contenido).hexdigest()]
    assert procesador.llamadas == 0  # no se intenta crear otra transacción
    assert recepcion.recibido and recepcion.id_transaccion == EXISTENTE
    assert recepcion.destino == configuracion.recibidos / "factura.xml"
    assert (configuracion.recibidos / "factura.xml").read_bytes() == contenido
    assert not ruta.exists() and not configuracion.no_recibidos.exists()
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and str(EXISTENTE) in avisos[0].getMessage()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    ("nombre_registrado", "canal", "caso"),
    [
        ("otro-nombre.xml", CanalRecepcion.CARPETA, "por la carpeta con otro nombre"),
        ("factura.xml", CanalRecepcion.API, "por la API con el mismo nombre"),
        ("factura.xml", None, "sin canal (registro anterior al canal)"),
    ],
)
def test_cualquier_otro_sha256_repetido_es_duplicado_y_va_a_no_recibidos(
    configuracion, caplog, nombre_registrado, canal, caso
):
    ruta = configuracion.entrada / "factura.xml"
    ruta.write_bytes(xml_factura("FV-DUP"))
    procesador = ProcesadorQueRechazaDuplicados()
    receptor, _ = _receptor(configuracion, DocumentoRegistrado(EXISTENTE, nombre_registrado, canal), procesador)

    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        recepcion = receptor.recibir(ruta)

    assert not recepcion.recibido, caso
    assert procesador.llamadas == 1
    assert (configuracion.no_recibidos / "factura.xml").exists()
    assert not configuracion.recibidos.exists()
    motivo = json.loads((configuracion.no_recibidos / ("factura.xml" + SUFIJO_MOTIVO)).read_text(encoding="utf-8"))
    assert motivo["tipo_error"] == "FacturaDuplicadaError"
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_sin_registro_previo_se_procesa_normalmente(configuracion):
    ruta = configuracion.entrada / "nueva.xml"
    ruta.write_bytes(xml_factura("FV-NUEVA"))
    nuevo = uuid.uuid4()
    receptor, consultas = _receptor(configuracion, None, lambda *_: nuevo)
    recepcion = receptor.recibir(ruta)
    assert len(consultas) == 1
    assert recepcion.recibido and recepcion.id_transaccion == nuevo and recepcion.motivo is None


def test_la_consulta_no_se_hace_si_el_documento_es_invalido(configuracion):
    ruta = configuracion.entrada / "malo.xml"
    ruta.write_bytes(b"<no-es-factura/>")
    receptor, consultas = _receptor(configuracion, None, lambda *_: uuid.uuid4())
    assert not receptor.recibir(ruta).recibido
    assert consultas == []
