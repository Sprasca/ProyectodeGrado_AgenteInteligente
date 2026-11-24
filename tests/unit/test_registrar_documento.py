"""Flujo único de registro: lo usan la API y la recepción automática (canal "api" o "carpeta")."""

import hashlib
import uuid

import pytest
from autenticacion_pruebas import USUARIOS, encabezados
from fabricas import xml_factura
from repositorio_memoria import RepositorioMemoria
from test_pdf_en_alerta import pdf_factura

from agente.application.registrar_documento import clasificar_documento, guardar_documento
from agente.domain.documento import DocumentoOriginal
from agente.domain.enums import CanalRecepcion, EstadoTransaccion, Rol, TipoDocumento, TipoEvento
from agente.infrastructure.dian.documentos import tipo_por_nombre
from agente.infrastructure.dian.parser import ParserUBLError


@pytest.mark.parametrize(
    ("nombre", "tipo"),
    [
        ("f.xml", TipoDocumento.XML),
        ("F.XML", TipoDocumento.XML),
        ("f.pdf", TipoDocumento.PDF),
        ("f.txt", None),
        (None, None),
    ],
)
def test_tipo_por_nombre(nombre, tipo):
    assert tipo_por_nombre(nombre) is tipo


def test_clasificar_documento_xml_y_pdf(catalogo):
    xml = clasificar_documento(xml_factura("FV-1"), TipoDocumento.XML, catalogo, usar_nlp=False)
    assert (xml.factura.numero, xml.estado) == ("FV-1", EstadoTransaccion.APROBADA_AUTOMATICA)
    pdf = clasificar_documento(pdf_factura(), TipoDocumento.PDF, catalogo, usar_nlp=False)
    assert pdf.factura.viene_de_pdf and pdf.estado is EstadoTransaccion.EN_ALERTA


def test_clasificar_documento_ilegible_lanza_el_error_del_lector(catalogo):
    with pytest.raises(ParserUBLError):
        clasificar_documento(b"<no-es-factura/>", TipoDocumento.XML, catalogo, usar_nlp=False)


def test_guardar_documento_sin_usuario_por_la_carpeta(catalogo):
    repo = RepositorioMemoria(catalogo)
    contenido = xml_factura("FV-1")
    resultado = clasificar_documento(contenido, TipoDocumento.XML, catalogo, usar_nlp=False)
    documento = DocumentoOriginal.recibido("f.xml", TipoDocumento.XML, contenido)
    id_ = guardar_documento(resultado, documento, repo, canal=CanalRecepcion.CARPETA)
    recibida, clasificada = repo.eventos(id_)
    assert (recibida.tipo, recibida.usuario_id, recibida.despues["canal"]) == (TipoEvento.RECIBIDA, None, "carpeta")
    assert recibida.despues["documento"]["sha256"] == hashlib.sha256(contenido).hexdigest()
    assert (clasificada.tipo, clasificada.usuario_id) == (TipoEvento.CLASIFICADA, None)


def test_la_api_registra_canal_api_con_el_usuario(entorno_api):
    r = entorno_api.cliente.post(
        "/facturas/clasificar",
        files={"archivo": ("f.xml", xml_factura("FV-1"), "application/xml")},
        params={"usar_nlp": "false"},
        headers=encabezados(Rol.AUXILIAR),
    )
    assert r.status_code == 200, r.text
    recibida = entorno_api.transacciones.eventos(uuid.UUID(r.json()["id"]))[0]
    assert (recibida.usuario_id, recibida.despues["canal"]) == (USUARIOS[Rol.AUXILIAR].id, "api")
