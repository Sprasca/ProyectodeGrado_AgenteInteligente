"""Endurecimiento: CORS solo para CORS_ORIGENES y /carpeta/procesar limitado a DIRECTORIO_ENTRADA."""

import os

import pytest
from autenticacion_pruebas import encabezados
from fabricas import xml_factura

from agente.config import Settings, get_settings
from agente.domain.enums import Rol

AUXILIAR = encabezados(Rol.AUXILIAR)
PERMITIDO = "http://permitido.test"  # CORS_ORIGENES de las pruebas (tests/conftest.py)


# --- CORS ----------------------------------------------------------------------------------------


def _preflight(cliente, origen):
    return cliente.options(
        "/transacciones",
        headers={
            "Origin": origen,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_cors_permite_los_origenes_configurados(entorno_api):
    r = _preflight(entorno_api.cliente, PERMITIDO)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == PERMITIDO
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


@pytest.mark.parametrize("origen", ["http://malicioso.test", "http://permitido.test.malicioso.test", "null"])
def test_cors_rechaza_otros_origenes(entorno_api, origen):
    r = _preflight(entorno_api.cliente, origen)
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    simple = entorno_api.cliente.get("/salud", headers={"Origin": origen})
    assert "access-control-allow-origin" not in simple.headers


def test_cors_expone_las_cabeceras_del_documento(entorno_api):
    r = entorno_api.cliente.get("/salud", headers={"Origin": PERMITIDO})
    assert r.headers["access-control-allow-origin"] == PERMITIDO
    assert "x-contenido-sha256" in r.headers["access-control-expose-headers"].lower()


def test_cors_vacio_por_defecto(monkeypatch, tmp_path):
    monkeypatch.delenv("CORS_ORIGENES", raising=False)
    monkeypatch.chdir(tmp_path)  # sin .env
    assert Settings().cors_origenes == []


def test_cors_lista_separada_por_comas(monkeypatch):
    monkeypatch.setenv("CORS_ORIGENES", " https://a.test/ , https://b.test ,, ")
    assert Settings().cors_origenes == ["https://a.test", "https://b.test"]


def test_cors_no_acepta_comodin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGENES", "https://a.test,*")
    with pytest.raises(ValueError, match="CORS_ORIGENES"):
        Settings()


# --- /carpeta/procesar dentro de DIRECTORIO_ENTRADA ------------------------------------------------


@pytest.fixture
def entrada(monkeypatch, tmp_path):
    """DIRECTORIO_ENTRADA temporal con una factura en facturas/ y otra en facturas/sub/; afuera, otra más."""
    raiz = tmp_path / "entrada"
    (raiz / "facturas" / "sub").mkdir(parents=True)
    (raiz / "facturas" / "f1.xml").write_bytes(xml_factura("FV-1"))
    (raiz / "facturas" / "sub" / "f2.xml").write_bytes(xml_factura("FV-2"))
    afuera = tmp_path / "afuera"
    afuera.mkdir()
    (afuera / "secreta.xml").write_bytes(xml_factura("FV-SECRETA"))
    monkeypatch.setenv("DIRECTORIO_ENTRADA", str(raiz))
    monkeypatch.setenv("DIRECTORIO_FACTURAS", str(raiz / "facturas"))
    get_settings.cache_clear()
    yield raiz, afuera
    get_settings.cache_clear()


def _procesar(cliente, ruta=None, recursivo=False):
    cuerpo = {"usar_nlp": False, "recursivo": recursivo}
    if ruta is not None:
        cuerpo["ruta"] = ruta
    return cliente.post("/carpeta/procesar", json=cuerpo, headers=AUXILIAR)


def _archivos(respuesta):
    return sorted(item["archivo"] for item in respuesta.json())


@pytest.mark.parametrize(
    ("ruta", "esperados"),
    [
        (None, ["f1.xml"]),  # DIRECTORIO_FACTURAS por defecto
        ("facturas", ["f1.xml"]),
        ("facturas/sub", ["f2.xml"]),
        ("facturas/sub/..", ["f1.xml"]),  # ".." que se queda adentro está bien
        (".", []),
    ],
)
def test_rutas_dentro_del_directorio_de_entrada(entorno_api, entrada, ruta, esperados):
    r = _procesar(entorno_api.cliente, ruta)
    assert r.status_code == 200, r.text
    assert _archivos(r) == esperados


def test_ruta_absoluta_dentro_del_directorio_de_entrada(entorno_api, entrada):
    raiz, _ = entrada
    r = _procesar(entorno_api.cliente, str(raiz / "facturas"), recursivo=True)
    assert r.status_code == 200
    assert _archivos(r) == ["f1.xml", "f2.xml"]


@pytest.mark.parametrize("ruta", ["..", "../afuera", "facturas/../../afuera", "facturas/sub/../../..", "..\\afuera"])
def test_rutas_con_puntos_fuera_del_directorio_403(entorno_api, entrada, ruta):
    r = _procesar(entorno_api.cliente, ruta)
    assert r.status_code == 403
    assert "fuera del directorio de entrada" in r.json()["detail"]


def test_ruta_absoluta_fuera_del_directorio_403(entorno_api, entrada):
    _, afuera = entrada
    assert _procesar(entorno_api.cliente, str(afuera)).status_code == 403


def test_directorio_de_facturas_fuera_de_la_entrada_403(entorno_api, entrada, monkeypatch):
    _, afuera = entrada
    monkeypatch.setenv("DIRECTORIO_FACTURAS", str(afuera))
    get_settings.cache_clear()
    assert _procesar(entorno_api.cliente).status_code == 403


def test_carpeta_inexistente_dentro_404(entorno_api, entrada):
    assert _procesar(entorno_api.cliente, "facturas/no-existe").status_code == 404


def test_enlace_simbolico_hacia_afuera_se_omite(entorno_api, entrada):
    raiz, afuera = entrada
    enlace = raiz / "facturas" / "enlace.xml"
    try:
        os.symlink(afuera / "secreta.xml", enlace)
    except (OSError, NotImplementedError):
        pytest.skip("Este sistema no permite crear enlaces simbólicos sin privilegios.")
    r = _procesar(entorno_api.cliente, "facturas")
    assert r.status_code == 200
    assert _archivos(r) == ["f1.xml"]


def test_carpeta_enlazada_hacia_afuera_403(entorno_api, entrada):
    raiz, afuera = entrada
    enlace = raiz / "facturas" / "otra"
    try:
        os.symlink(afuera, enlace, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Este sistema no permite crear enlaces simbólicos sin privilegios.")
    assert _procesar(entorno_api.cliente, "facturas/otra").status_code == 403
