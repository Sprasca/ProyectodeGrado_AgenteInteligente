"""API con TestClient y RepositorioMemoria: sin PostgreSQL, sin Groq."""

import re
import uuid
from pathlib import Path

import pytest
from fabricas import (
    CUENTA_NO_SEMBRADA,
    catalogo_con_cuenta_no_sembrada,
    resultado_alerta_con_asiento,
    resultado_alerta_sin_asiento,
    resultado_aprobado,
)
from fastapi.testclient import TestClient
from repositorio_memoria import RepositorioMemoria

from agente.api.main import app, get_repositorio
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError

XML = (Path(__file__).resolve().parents[1] / "fixtures" / "factura_comercio_contado.xml").read_bytes()
SIN_NLP = {"usar_nlp": "false"}


@pytest.fixture
def repo(catalogo):
    return RepositorioMemoria(catalogo)


@pytest.fixture
def cliente(repo):
    app.dependency_overrides[get_repositorio] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.clear()


def _subir(cliente, nombre="factura.xml", contenido=XML, **params):
    return cliente.post(
        "/facturas/clasificar", files={"archivo": (nombre, contenido, "application/octet-stream")}, params=params
    )


def _cuerpo_correccion(catalogo, debito="119000"):
    return {
        "partidas": [
            {"cuenta": catalogo.resolver("caja"), "naturaleza": "DEBITO", "valor": debito},
            {"cuenta": "4135", "naturaleza": "CREDITO", "valor": "100000"},
            {"cuenta": catalogo.cuenta_iva(), "naturaleza": "CREDITO", "valor": "19000"},
        ]
    }


# --- /salud y /facturas/clasificar --------------------------------------------------------


def test_salud(cliente):
    r = cliente.get("/salud")
    assert r.status_code == 200
    assert r.json() == {"estado": "ok"}


def test_clasificar_guarda_y_devuelve_id(cliente, repo):
    r = _subir(cliente, **SIN_NLP)
    assert r.status_code == 200
    datos = r.json()
    assert datos["estado"] == "APROBADA_AUTOMATICA"
    assert datos["lineas"][0]["estado"] == "CLASIFICADA"
    guardada = repo.obtener(uuid.UUID(datos["id"]))
    assert guardada is not None
    assert guardada.numero_factura == "FV-1001"


def test_clasificar_sin_guardar_no_toca_el_repositorio(cliente, repo):
    r = _subir(cliente, guardar="false", **SIN_NLP)
    assert r.status_code == 200
    assert "id" not in r.json()
    assert repo.listar() == []


def test_clasificar_factura_duplicada_409(cliente):
    assert _subir(cliente, **SIN_NLP).status_code == 200
    r = _subir(cliente, **SIN_NLP)
    assert r.status_code == 409
    assert "FV-1001" in r.json()["detail"]


def test_clasificar_factura_sin_numero_queda_en_alerta_con_numero_provisional(cliente, repo):
    sin_numero = XML.replace(b"<cbc:ID>FV-1001</cbc:ID>", b"")
    assert sin_numero != XML

    primera = _subir(cliente, contenido=sin_numero, **SIN_NLP)
    assert primera.status_code == 200
    datos = primera.json()
    assert re.fullmatch(r"SIN-NUMERO-[0-9a-f]{8}", datos["factura"]["numero"])
    assert datos["estado"] == "EN_ALERTA"
    assert "Factura sin número. Se asignó un identificador provisional." in datos["motivos_alerta"]

    guardada = cliente.get(f"/transacciones/{datos['id']}").json()
    assert guardada["numero_factura"] == datos["factura"]["numero"]
    assert guardada["estado"] == "EN_ALERTA"

    # Cada carga recibe un identificador nuevo: no se rechaza como duplicada.
    segunda = _subir(cliente, contenido=sin_numero, **SIN_NLP)
    assert segunda.status_code == 200
    assert segunda.json()["factura"]["numero"] != datos["factura"]["numero"]


def test_clasificar_extension_no_soportada_415(cliente):
    r = _subir(cliente, nombre="factura.txt", contenido=b"hola")
    assert r.status_code == 415


def test_clasificar_xml_invalido_422(cliente, repo):
    r = _subir(cliente, contenido=b"<no-es-factura/>", **SIN_NLP)
    assert r.status_code == 422
    assert repo.listar() == []


class RepositorioCaido:
    """Simula PostgreSQL inaccesible."""

    def _caer(self, *_args, **_kwargs):
        raise RepositorioNoDisponibleError("No hay conexión con PostgreSQL (simulado).")

    guardar = obtener = listar = aprobar = corregir = _caer


@pytest.fixture
def cliente_sin_bd():
    app.dependency_overrides[get_repositorio] = RepositorioCaido
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_clasificar_con_base_inaccesible_503(cliente_sin_bd):
    r = _subir(cliente_sin_bd, **SIN_NLP)
    assert r.status_code == 503
    assert "PostgreSQL" in r.json()["detail"]


def test_listar_con_base_inaccesible_503(cliente_sin_bd):
    assert cliente_sin_bd.get("/transacciones").status_code == 503


def test_clasificar_sin_guardar_funciona_sin_base(cliente_sin_bd):
    assert _subir(cliente_sin_bd, guardar="false", **SIN_NLP).status_code == 200


# --- GET /transacciones -------------------------------------------------------------------


def test_listar_todas_y_filtrar_por_estado(cliente, repo, catalogo):
    id_ok = repo.guardar(resultado_aprobado(catalogo, "FV-A"))
    id_alerta = repo.guardar(resultado_alerta_sin_asiento(catalogo, "FV-B"))

    todas = cliente.get("/transacciones")
    assert todas.status_code == 200
    assert {t["id"] for t in todas.json()} == {str(id_ok), str(id_alerta)}

    alerta = cliente.get("/transacciones", params={"estado": "EN_ALERTA"})
    assert alerta.status_code == 200
    assert [t["id"] for t in alerta.json()] == [str(id_alerta)]


def test_listar_estado_desconocido_422(cliente):
    assert cliente.get("/transacciones", params={"estado": "INVENTADO"}).status_code == 422


def test_obtener_trae_partidas_y_detalle_lineas(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_aprobado(catalogo, "FV-A"))
    r = cliente.get(f"/transacciones/{id_}")
    assert r.status_code == 200
    datos = r.json()
    assert datos["numero_factura"] == "FV-A"
    assert len(datos["partidas"]) == 3
    assert datos["detalle_lineas"][0]["cuenta_ingreso"] == "4135"


def test_obtener_inexistente_404(cliente):
    assert cliente.get(f"/transacciones/{uuid.uuid4()}").status_code == 404


def test_obtener_id_invalido_422(cliente):
    assert cliente.get("/transacciones/no-es-un-uuid").status_code == 422


# --- POST /transacciones/{id}/aprobar -----------------------------------------------------


def test_aprobar_200(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, "FV-A"))
    r = cliente.post(f"/transacciones/{id_}/aprobar")
    assert r.status_code == 200
    assert r.json()["estado"] == "APROBADA_CONTADOR"


def test_aprobar_inexistente_404(cliente):
    assert cliente.post(f"/transacciones/{uuid.uuid4()}/aprobar").status_code == 404


def test_aprobar_transicion_invalida_409(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_aprobado(catalogo, "FV-A"))
    assert cliente.post(f"/transacciones/{id_}/aprobar").status_code == 409


def test_aprobar_sin_partidas_409(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, "FV-A"))
    assert cliente.post(f"/transacciones/{id_}/aprobar").status_code == 409


# --- POST /transacciones/{id}/corregir ----------------------------------------------------


def test_corregir_200(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, "FV-A"))
    r = cliente.post(f"/transacciones/{id_}/corregir", json=_cuerpo_correccion(catalogo))
    assert r.status_code == 200
    datos = r.json()
    assert datos["estado"] == "CORREGIDA_CONTADOR"
    assert [p["cuenta"] for p in datos["partidas"]] == [catalogo.resolver("caja"), "4135", catalogo.cuenta_iva()]


def test_obtener_expone_asiento_propuesto_y_corregido_en(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_aprobado(catalogo, "FV-A"))
    antes = cliente.get(f"/transacciones/{id_}").json()
    assert antes["asiento_propuesto"] is None
    assert antes["corregido_en"] is None
    propuesta = [p["cuenta"] for p in antes["partidas"]]

    cuerpo = _cuerpo_correccion(catalogo)
    cuerpo["partidas"][0]["cuenta"] = catalogo.resolver("bancos")
    assert cliente.post(f"/transacciones/{id_}/corregir", json=cuerpo).status_code == 200

    despues = cliente.get(f"/transacciones/{id_}").json()
    assert [p["cuenta"] for p in despues["asiento_propuesto"]] == propuesta
    assert despues["corregido_en"] is not None
    assert despues["partidas"][0]["cuenta"] == catalogo.resolver("bancos")


def test_corregir_inexistente_404(cliente, catalogo):
    r = cliente.post(f"/transacciones/{uuid.uuid4()}/corregir", json=_cuerpo_correccion(catalogo))
    assert r.status_code == 404


def test_corregir_transicion_invalida_409(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, "FV-A"))
    repo.aprobar(id_)
    r = cliente.post(f"/transacciones/{id_}/corregir", json=_cuerpo_correccion(catalogo))
    assert r.status_code == 409


def test_corregir_asiento_descuadrado_422(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, "FV-A"))
    r = cliente.post(f"/transacciones/{id_}/corregir", json=_cuerpo_correccion(catalogo, debito="118000"))
    assert r.status_code == 422
    assert "descuadrado" in r.json()["detail"]
    assert repo.obtener(id_).estado.value == "EN_ALERTA"


def test_corregir_cuenta_inexistente_422(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, "FV-A"))
    cuerpo = {
        "partidas": [
            {"cuenta": "9999", "naturaleza": "DEBITO", "valor": "100"},
            {"cuenta": "4135", "naturaleza": "CREDITO", "valor": "100"},
        ]
    }
    r = cliente.post(f"/transacciones/{id_}/corregir", json=cuerpo)
    assert r.status_code == 422
    assert "9999" in r.json()["detail"]


def _usar_repositorio(repositorio):
    app.dependency_overrides[get_repositorio] = lambda: repositorio
    return TestClient(app)


def test_clasificar_cuenta_no_sembrada_409(cliente, catalogo):
    # La base no tiene 4135 (p. ej. el contador la agregó a data/puc y no se volvió a sembrar).
    sin_4135 = RepositorioMemoria(catalogo, cuentas_en_bd={c.codigo for c in catalogo.cuentas} - {"4135"})
    r = _subir(_usar_repositorio(sin_4135), **SIN_NLP)
    assert r.status_code == 409
    assert r.json()["detail"] == "La cuenta 4135 no está en la base de datos. Ejecute scripts/sembrar_puc.py."
    assert sin_4135.listar() == []


def test_corregir_cuenta_no_sembrada_409(cliente, catalogo):
    repositorio = RepositorioMemoria(
        catalogo_con_cuenta_no_sembrada(catalogo), cuentas_en_bd={c.codigo for c in catalogo.cuentas}
    )
    id_ = repositorio.guardar(resultado_alerta_sin_asiento(catalogo, "FV-A"))
    cuerpo = _cuerpo_correccion(catalogo)
    cuerpo["partidas"][1]["cuenta"] = CUENTA_NO_SEMBRADA

    r = _usar_repositorio(repositorio).post(f"/transacciones/{id_}/corregir", json=cuerpo)
    assert r.status_code == 409
    assert r.json()["detail"] == (
        f"La cuenta {CUENTA_NO_SEMBRADA} no está en la base de datos. Ejecute scripts/sembrar_puc.py."
    )
    assert repositorio.obtener(id_).estado.value == "EN_ALERTA"


def test_corregir_cuerpo_mal_formado_422(cliente, repo, catalogo):
    id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, "FV-A"))
    cuerpo = {"partidas": [{"cuenta": "1105", "naturaleza": "OTRA", "valor": "100"}]}
    assert cliente.post(f"/transacciones/{id_}/corregir", json=cuerpo).status_code == 422
