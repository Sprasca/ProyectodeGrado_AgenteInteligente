"""Recepción automática (RF01): validación, movimiento a recibidos/ o no_recibidos/ y registro del motivo."""

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fabricas import xml_factura
from test_pdf_en_alerta import pdf_factura

from agente.application.recepcion import SUFIJO_MOTIVO, ConfiguracionRecepcion, ReceptorFacturas
from agente.domain.enums import TipoDocumento
from agente.domain.errors import FacturaDuplicadaError

AHORA = datetime(2026, 11, 23, 15, 30, 0, tzinfo=UTC)


class ProcesadorFalso:
    """Registra lo que recibe; puede fallar con la excepción indicada."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.llamadas: list[tuple[bytes, str, TipoDocumento]] = []

    def __call__(self, contenido, nombre, tipo):
        self.llamadas.append((contenido, nombre, tipo))
        if self.error is not None:
            raise self.error
        return uuid.UUID(int=len(self.llamadas))


@pytest.fixture
def carpetas(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    return ConfiguracionRecepcion(
        entrada=entrada,
        recibidos=entrada / "recibidos",
        no_recibidos=entrada / "no_recibidos",
        tamano_maximo=1024 * 1024,
        espera_estabilidad=0,
    )


def _receptor(configuracion, procesador=None, dormir=lambda _: None):
    return ReceptorFacturas(configuracion, procesador or ProcesadorFalso(), dormir=dormir, ahora=lambda: AHORA)


def _dejar(configuracion, nombre, contenido) -> Path:
    ruta = configuracion.entrada / nombre
    ruta.write_bytes(contenido)
    return ruta


def _motivo(configuracion, nombre):
    return json.loads((configuracion.no_recibidos / (nombre + SUFIJO_MOTIVO)).read_text(encoding="utf-8"))


# --- Recibidos ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "contenido", "tipo"),
    [("f.xml", xml_factura("FV-1"), TipoDocumento.XML), ("f.pdf", pdf_factura(), TipoDocumento.PDF)],
)
def test_archivo_valido_se_procesa_y_va_a_recibidos(carpetas, caplog, nombre, contenido, tipo):
    procesador = ProcesadorFalso()
    ruta = _dejar(carpetas, nombre, contenido)
    with caplog.at_level(logging.INFO, logger="agente.recepcion"):
        recepcion = _receptor(carpetas, procesador).recibir(ruta)

    assert recepcion.recibido and recepcion.id_transaccion == uuid.UUID(int=1)
    assert procesador.llamadas == [(contenido, nombre, tipo)]
    assert not ruta.exists()
    assert (carpetas.recibidos / nombre).read_bytes() == contenido
    assert recepcion.destino == carpetas.recibidos / nombre
    assert not carpetas.no_recibidos.exists()
    assert f"Factura recibida: {nombre}" in caplog.text


def test_no_sobrescribe_un_archivo_con_el_mismo_nombre(carpetas):
    carpetas.recibidos.mkdir()
    (carpetas.recibidos / "f.xml").write_bytes(b"anterior")
    recepcion = _receptor(carpetas).recibir(_dejar(carpetas, "f.xml", xml_factura("FV-2")))
    assert (carpetas.recibidos / "f.xml").read_bytes() == b"anterior"
    assert recepcion.destino.name == "f__20261123T153000000000Z.xml"
    assert recepcion.destino.read_bytes() == xml_factura("FV-2")


# --- No recibidos, con su motivo ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contenido", "fragmento"),
    [
        (b"<no es xml", "no está bien formado"),
        (b"<CreditNote><ID>1</ID></CreditNote>", "No es una factura electrónica"),
        (xml_factura("FV-1").replace(b"<cbc:ID>FV-1</cbc:ID>", b""), "cbc:ID"),
    ],
)
def test_xml_invalido_va_a_no_recibidos_con_motivo_y_sin_procesarse(carpetas, caplog, contenido, fragmento):
    procesador = ProcesadorFalso()
    ruta = _dejar(carpetas, "malo.xml", contenido)
    with caplog.at_level(logging.ERROR, logger="agente.recepcion"):
        recepcion = _receptor(carpetas, procesador).recibir(ruta)

    assert not recepcion.recibido and fragmento in recepcion.motivo
    assert procesador.llamadas == []
    assert not ruta.exists()
    assert (carpetas.no_recibidos / "malo.xml").read_bytes() == contenido
    motivo = _motivo(carpetas, "malo.xml")
    assert motivo["archivo"] == "malo.xml"
    assert fragmento in motivo["motivo"]
    assert motivo["tipo_error"] == "DocumentoInvalidoError"
    assert motivo["fecha"] == "2026-11-23T15:30:00+00:00"
    assert motivo["intentos"] == 1
    # Alerta técnica: registro de nivel ERROR con el motivo.
    assert any(r.levelno == logging.ERROR and "malo.xml" in r.getMessage() for r in caplog.records)


def test_pdf_incompleto_va_a_no_recibidos(carpetas):
    contenido = pdf_factura()
    recepcion = _receptor(carpetas).recibir(_dejar(carpetas, "cortado.pdf", contenido[: len(contenido) // 2]))
    assert not recepcion.recibido
    assert "incompleto" in _motivo(carpetas, "cortado.pdf")["motivo"]


def test_archivo_demasiado_grande_va_a_no_recibidos(carpetas):
    grande = xml_factura("FV-1") + b"<!--" + b"x" * carpetas.tamano_maximo + b"-->"
    procesador = ProcesadorFalso()
    recepcion = _receptor(carpetas, procesador).recibir(_dejar(carpetas, "grande.xml", grande))
    assert not recepcion.recibido and "tamaño máximo" in recepcion.motivo
    assert procesador.llamadas == []
    assert (carpetas.no_recibidos / "grande.xml").stat().st_size == len(grande)  # se mueve completo


def test_factura_duplicada_va_a_no_recibidos(carpetas):
    procesador = ProcesadorFalso(FacturaDuplicadaError("La factura FV-1 ya fue registrada."))
    recepcion = _receptor(carpetas, procesador).recibir(_dejar(carpetas, "f.xml", xml_factura("FV-1")))
    assert not recepcion.recibido
    motivo = _motivo(carpetas, "f.xml")
    assert (motivo["tipo_error"], motivo["motivo"]) == ("FacturaDuplicadaError", "La factura FV-1 ya fue registrada.")


def test_un_error_inesperado_no_pierde_el_archivo(carpetas):
    recepcion = _receptor(carpetas, ProcesadorFalso(RuntimeError("fallo inesperado"))).recibir(
        _dejar(carpetas, "f.xml", xml_factura("FV-1"))
    )
    assert not recepcion.recibido
    assert (carpetas.no_recibidos / "f.xml").exists()
    assert _motivo(carpetas, "f.xml")["tipo_error"] == "RuntimeError"


def test_motivo_de_dos_archivos_con_el_mismo_nombre_no_se_pisa(carpetas):
    receptor = _receptor(carpetas)
    receptor.recibir(_dejar(carpetas, "malo.xml", b"<uno"))
    segunda = receptor.recibir(_dejar(carpetas, "malo.xml", b"<dos"))
    assert segunda.destino.name == "malo__20261123T153000000000Z.xml"
    assert segunda.destino.with_name(segunda.destino.name + SUFIJO_MOTIVO).exists()
    assert (carpetas.no_recibidos / ("malo.xml" + SUFIJO_MOTIVO)).exists()


def test_archivo_que_desaparece_no_falla(carpetas):
    recepcion = _receptor(carpetas).recibir(carpetas.entrada / "ya-no-esta.xml")
    assert not recepcion.recibido and recepcion.destino is None
    assert not carpetas.no_recibidos.exists()


# --- Qué archivos se reciben ------------------------------------------------------------------------


def test_candidatos_solo_xml_y_pdf_en_la_raiz_de_la_entrada(carpetas):
    receptor = _receptor(carpetas)
    xml = _dejar(carpetas, "a.xml", b"x")
    pdf = _dejar(carpetas, "B.PDF", b"x")
    otro = _dejar(carpetas, "nota.txt", b"x")
    carpetas.recibidos.mkdir()
    ya_recibido = carpetas.recibidos / "c.xml"
    ya_recibido.write_bytes(b"x")
    motivo = carpetas.entrada / ("a.xml" + SUFIJO_MOTIVO)
    motivo.write_text("{}")
    assert receptor.es_candidato(xml) and receptor.es_candidato(pdf)
    assert not receptor.es_candidato(otro)
    assert not receptor.es_candidato(ya_recibido)
    assert not receptor.es_candidato(motivo)
    assert not receptor.es_candidato(carpetas.recibidos)
    # En orden de llegada (fecha de modificación), no alfabético.
    os.utime(xml, ns=(1_000_000_000, 1_000_000_000))
    os.utime(pdf, ns=(2_000_000_000, 2_000_000_000))
    assert receptor.pendientes() == [xml, pdf]
    os.utime(xml, ns=(3_000_000_000, 3_000_000_000))
    assert receptor.pendientes() == [pdf, xml]


def test_pendientes_con_la_entrada_inexistente(carpetas):
    carpetas.entrada.rmdir()
    assert _receptor(carpetas).pendientes() == []
