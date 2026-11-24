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


# --- Duplicado real: el mismo SHA-256 ya está en recibidos/ ---------------------------------------------------


def test_si_el_mismo_sha256_ya_esta_en_recibidos_es_un_duplicado_real(configuracion, caplog):
    contenido = xml_factura("FV-COPIA")
    configuracion.recibidos.mkdir()
    (configuracion.recibidos / "factura__20261124T100000000000Z.xml").write_bytes(contenido)  # nombre con marca
    ruta = configuracion.entrada / "factura.xml"
    ruta.write_bytes(contenido)
    procesador = ProcesadorQueRechazaDuplicados()
    registro = DocumentoRegistrado(EXISTENTE, "factura.xml", CanalRecepcion.CARPETA)
    receptor, _ = _receptor(configuracion, registro, procesador)

    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        recepcion = receptor.recibir(ruta)

    assert not recepcion.recibido
    assert procesador.llamadas == 1
    assert (configuracion.no_recibidos / "factura.xml").read_bytes() == contenido
    motivo = json.loads((configuracion.no_recibidos / ("factura.xml" + SUFIJO_MOTIVO)).read_text(encoding="utf-8"))
    assert motivo["tipo_error"] == "FacturaDuplicadaError"
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert sorted(p.name for p in configuracion.recibidos.iterdir()) == ["factura__20261124T100000000000Z.xml"]


def test_un_archivo_distinto_del_mismo_tamano_en_recibidos_no_cuenta(configuracion):
    contenido = xml_factura("FV-AAAA")
    otro = xml_factura("FV-BBBB")
    assert len(contenido) == len(otro) and contenido != otro
    configuracion.recibidos.mkdir()
    (configuracion.recibidos / "otra.xml").write_bytes(otro)
    ruta = configuracion.entrada / "factura.xml"
    ruta.write_bytes(contenido)
    registro = DocumentoRegistrado(EXISTENTE, "factura.xml", CanalRecepcion.CARPETA)
    receptor, _ = _receptor(configuracion, registro, ProcesadorQueRechazaDuplicados())
    recepcion = receptor.recibir(ruta)
    assert recepcion.recibido and recepcion.id_transaccion == EXISTENTE


class RegistroEnMemoria:
    """Procesador + consulta que comparten un registro por SHA-256, como la base de datos."""

    def __init__(self):
        self.por_sha256: dict[str, DocumentoRegistrado] = {}

    def procesar(self, contenido, nombre, tipo):
        sha256 = hashlib.sha256(contenido).hexdigest()
        if sha256 in self.por_sha256:
            raise FacturaDuplicadaError(f"El documento con SHA-256 {sha256} ya fue registrado.")
        id_ = uuid.uuid4()
        self.por_sha256[sha256] = DocumentoRegistrado(id_, nombre, CanalRecepcion.CARPETA)
        return id_

    def consultar(self, sha256):
        return self.por_sha256.get(sha256)


def test_el_mismo_archivo_copiado_dos_veces_seguidas_queda_una_vez_en_recibidos_y_otra_en_no_recibidos(
    configuracion, caplog
):
    """Lo que se reportó: copiar dos veces FV-2001.xml en entrada/ no debe dar dos "recibidas"."""
    registro = RegistroEnMemoria()
    receptor = ReceptorFacturas(
        configuracion, registro.procesar, dormir=lambda _: None, consultar_registro=registro.consultar
    )
    contenido = xml_factura("FV-2001")
    ruta = configuracion.entrada / "FV-2001.xml"

    ruta.write_bytes(contenido)
    primera = receptor.recibir(ruta)
    ruta.write_bytes(contenido)  # la segunda copia, con el mismo nombre
    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        segunda = receptor.recibir(ruta)

    assert primera.recibido and not segunda.recibido
    assert [p.name for p in configuracion.recibidos.iterdir()] == ["FV-2001.xml"]
    no_recibidos = sorted(p.name for p in configuracion.no_recibidos.iterdir())
    assert no_recibidos == ["FV-2001.xml", "FV-2001.xml" + SUFIJO_MOTIVO]
    assert len(registro.por_sha256) == 1
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_registrada_y_sin_mover_y_luego_copiada_otra_vez(configuracion, monkeypatch, caplog):
    """No se pudo mover → se recupera a recibidos/ (WARNING); una copia posterior ya es duplicado (ERROR)."""
    import agente.application.recepcion as recepcion

    registro = RegistroEnMemoria()
    receptor = ReceptorFacturas(
        configuracion, registro.procesar, dormir=lambda _: None, consultar_registro=registro.consultar
    )
    contenido = xml_factura("FV-2002")
    ruta = configuracion.entrada / "FV-2002.xml"
    ruta.write_bytes(contenido)

    mover = recepcion.shutil.move
    monkeypatch.setattr(recepcion.shutil, "move", lambda *_: (_ for _ in ()).throw(PermissionError("bloqueado")))
    receptor.recibir(ruta)  # se registra, pero queda en entrada/
    assert ruta.exists() and len(registro.por_sha256) == 1
    monkeypatch.setattr(recepcion.shutil, "move", mover)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        recuperada = receptor.recibir(ruta)
    assert recuperada.recibido and [r.levelno for r in caplog.records] == [logging.WARNING]
    assert (configuracion.recibidos / "FV-2002.xml").exists()

    ruta.write_bytes(contenido)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        copia = receptor.recibir(ruta)
    assert not copia.recibido and [r.levelno for r in caplog.records] == [logging.ERROR]
    assert (configuracion.no_recibidos / "FV-2002.xml").exists()
    assert len(registro.por_sha256) == 1

