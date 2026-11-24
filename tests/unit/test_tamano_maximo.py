"""Límite de tamaño de los documentos (TAMANO_MAXIMO_DOCUMENTO_MB, 10 MB por defecto)."""

import pytest
from autenticacion_pruebas import encabezados
from fabricas import xml_factura

from agente.config import Settings, get_settings
from agente.domain.enums import Rol


def _subir(entorno, contenido, nombre="f.xml"):
    return entorno.cliente.post(
        "/facturas/clasificar",
        files={"archivo": (nombre, contenido, "application/octet-stream")},
        params={"usar_nlp": "false"},
        headers=encabezados(Rol.AUXILIAR),
    )


@pytest.fixture
def limite_1_mb(monkeypatch):
    monkeypatch.setenv("TAMANO_MAXIMO_DOCUMENTO_MB", "1")
    get_settings.cache_clear()
    yield 1024 * 1024
    get_settings.cache_clear()


def test_por_defecto_10_mb(monkeypatch, tmp_path):
    monkeypatch.delenv("TAMANO_MAXIMO_DOCUMENTO_MB", raising=False)
    monkeypatch.chdir(tmp_path)  # sin .env
    settings = Settings()
    assert settings.tamano_maximo_documento_mb == 10
    assert settings.tamano_maximo_documento == 10 * 1024 * 1024


@pytest.mark.parametrize("valor", ["0", "-1", "diez", "1.5"])
def test_valor_invalido(monkeypatch, valor):
    monkeypatch.setenv("TAMANO_MAXIMO_DOCUMENTO_MB", valor)
    with pytest.raises(ValueError, match="TAMANO_MAXIMO_DOCUMENTO_MB"):
        Settings()


def test_archivo_mas_grande_que_el_limite_413_y_no_se_guarda(entorno_api, limite_1_mb):
    # Un XML válido inflado con un comentario hasta pasar el límite por un byte.
    base = xml_factura("FV-GRANDE")
    relleno = b"<!--" + b"x" * (limite_1_mb - len(base) - 7 + 1) + b"-->"
    contenido = base + relleno
    assert len(contenido) == limite_1_mb + 1
    r = _subir(entorno_api, contenido)
    assert r.status_code == 413
    assert "1 MB" in r.json()["detail"]
    assert entorno_api.transacciones.listar() == []


def test_archivo_justo_en_el_limite_se_acepta(entorno_api, limite_1_mb):
    base = xml_factura("FV-EXACTO")
    contenido = base + b"<!--" + b"x" * (limite_1_mb - len(base) - 7) + b"-->"
    assert len(contenido) == limite_1_mb
    assert _subir(entorno_api, contenido).status_code == 200


def test_la_extension_se_revisa_antes_del_tamano(entorno_api, limite_1_mb):
    assert _subir(entorno_api, b"x" * (limite_1_mb + 10), nombre="f.txt").status_code == 415
