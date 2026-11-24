"""/carpeta/procesar con recursivo=true no reprocesa recibidos/ ni no_recibidos/ de la recepción automática."""

import pytest
from autenticacion_pruebas import encabezados
from fabricas import xml_factura

from agente.config import get_settings
from agente.domain.enums import Rol
from agente.infrastructure.dian.lector_carpeta import leer_carpeta


@pytest.fixture
def entrada(monkeypatch, tmp_path):
    """entrada/ con una factura nueva, una en sub/ y otras en recibidos/ y no_recibidos/."""
    raiz = tmp_path / "entrada"
    for carpeta in ("sub", "recibidos", "no_recibidos"):
        (raiz / carpeta).mkdir(parents=True)
    (raiz / "nueva.xml").write_bytes(xml_factura("FV-NUEVA"))
    (raiz / "sub" / "en-sub.xml").write_bytes(xml_factura("FV-SUB"))
    (raiz / "recibidos" / "ya-recibida.xml").write_bytes(xml_factura("FV-YA"))
    (raiz / "no_recibidos" / "rechazada.xml").write_bytes(b"<malo")
    monkeypatch.setenv("DIRECTORIO_ENTRADA", str(raiz))
    monkeypatch.setenv("DIRECTORIO_FACTURAS", str(raiz))
    monkeypatch.delenv("DIRECTORIO_RECIBIDOS", raising=False)
    monkeypatch.delenv("DIRECTORIO_NO_RECIBIDOS", raising=False)
    get_settings.cache_clear()
    yield raiz
    get_settings.cache_clear()


def _procesar(cliente, **cuerpo):
    r = cliente.post("/carpeta/procesar", json={"usar_nlp": False, **cuerpo}, headers=encabezados(Rol.AUXILIAR))
    assert r.status_code == 200, r.text
    return sorted(item["archivo"] for item in r.json())


def test_recursivo_omite_recibidos_y_no_recibidos(entorno_api, entrada):
    assert _procesar(entorno_api.cliente, recursivo=True) == ["en-sub.xml", "nueva.xml"]


def test_sin_recursivo_no_cambia(entorno_api, entrada):
    assert _procesar(entorno_api.cliente) == ["nueva.xml"]


def test_pedir_explicitamente_la_carpeta_recibidos_si_se_respeta(entorno_api, entrada):
    assert _procesar(entorno_api.cliente, ruta="recibidos", recursivo=True) == ["ya-recibida.xml"]


def test_carpetas_de_recepcion_configuradas_en_otro_lugar_tambien_se_omiten(entorno_api, entrada, monkeypatch):
    (entrada / "archivo-ok").mkdir()
    (entrada / "archivo-ok" / "procesada.xml").write_bytes(xml_factura("FV-OK"))
    monkeypatch.setenv("DIRECTORIO_RECIBIDOS", str(entrada / "archivo-ok"))
    get_settings.cache_clear()
    # recibidos/ ya no es una carpeta de recepción: se lee; archivo-ok/ sí lo es: se omite.
    assert _procesar(entorno_api.cliente, recursivo=True) == ["en-sub.xml", "nueva.xml", "ya-recibida.xml"]


def test_leer_carpeta_con_exclusiones(entrada):
    excluir = [entrada / "recibidos", entrada / "no_recibidos"]
    nombres = sorted(a.ruta.name for a in leer_carpeta(entrada, recursivo=True, excluir=excluir))
    assert nombres == ["en-sub.xml", "nueva.xml"]
    todos = sorted(a.ruta.name for a in leer_carpeta(entrada, recursivo=True))
    assert todos == ["en-sub.xml", "nueva.xml", "rechazada.xml", "ya-recibida.xml"]
