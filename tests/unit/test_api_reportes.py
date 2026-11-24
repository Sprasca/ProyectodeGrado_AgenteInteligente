"""API de reportes diarios (RF06, RF07): el ciclo completo con repositorios en memoria.

Los 401 (sin token) y 403 (rol sin permiso) de cada ruta están en test_autenticacion.py y test_permisos.py.
"""

import hashlib
import uuid
import xml.etree.ElementTree as ET

import pytest
from autenticacion_pruebas import USUARIOS, encabezados
from fabricas import partidas_correctas, resultado_alerta_con_asiento, resultado_aprobado

from agente.domain.enums import EstadoReporte, Rol

C, A = Rol.CONTADOR, Rol.AUXILIAR


@pytest.fixture
def api(entorno_api, catalogo):
    entorno_api.catalogo = catalogo
    return entorno_api


def _aprobadas(api, *numeros):
    return [api.transacciones.guardar(resultado_aprobado(api.catalogo, n)) for n in numeros]


def _consolidar(api, rol=A):
    return api.cliente.post("/reportes/consolidar", headers=encabezados(rol))


def _consolidado(api):
    ids = _aprobadas(api, "FV-1", "FV-2")
    r = _consolidar(api)
    assert r.status_code == 201, r.text
    return r.json(), ids


# --- consolidar (RF06) -------------------------------------------------------------------------------------


@pytest.mark.parametrize("rol", [A, C])
def test_auxiliar_y_contador_consolidan(api, rol):
    ids = _aprobadas(api, "FV-1", "FV-2")
    r = _consolidar(api, rol)
    assert r.status_code == 201
    reporte = r.json()
    assert reporte["estado"] == "CONSOLIDADO"
    assert reporte["numero_transacciones"] == 2
    assert sorted(reporte["transacciones"]) == sorted(str(i) for i in ids)
    assert reporte["total_debito"] == reporte["total_credito"]
    assert {"codigo", "cuenta", "debito", "credito"} <= set(reporte["movimientos"][0])
    assert {"cuenta", "debito", "credito"} <= set(reporte["subtotales"][0])
    (evento,) = api.cliente.get(f"/reportes/{reporte['id']}/eventos", headers=encabezados(C)).json()
    assert (evento["tipo"], evento["usuario_id"]) == ("CONSOLIDADO", USUARIOS[rol].id)


def test_con_alertas_sin_resolver_responde_409_con_el_numero_pendiente(api):
    _aprobadas(api, "FV-1")
    for numero in ("FV-A1", "FV-A2"):
        api.transacciones.guardar(resultado_alerta_con_asiento(api.catalogo, numero))
    r = _consolidar(api)
    assert r.status_code == 409
    assert r.json()["pendientes"] == 2 and "EN_ALERTA" in r.json()["detail"]
    assert api.reportes.listar() == []


def test_sin_transacciones_responde_409_y_no_crea_un_reporte_vacio(api):
    r = _consolidar(api)
    assert r.status_code == 409 and "reporte vacío" in r.json()["detail"]
    assert api.cliente.get("/reportes", headers=encabezados(A)).json() == []


# --- consultar ---------------------------------------------------------------------------------------------


def test_listar_filtrar_y_obtener(api):
    reporte, _ = _consolidado(api)
    assert [r["id"] for r in api.cliente.get("/reportes", headers=encabezados(A)).json()] == [reporte["id"]]
    assert api.cliente.get("/reportes?estado=EXPORTADO", headers=encabezados(A)).json() == []
    assert api.cliente.get(f"/reportes/{reporte['id']}", headers=encabezados(A)).json() == reporte
    assert api.cliente.get("/reportes?estado=AUTORIZADO", headers=encabezados(A)).status_code == 422


def test_reporte_inexistente_404(api):
    otro = "11111111-2222-3333-4444-555555555555"
    for ruta in (f"/reportes/{otro}", f"/reportes/{otro}/eventos", f"/reportes/{otro}/xml"):
        assert api.cliente.get(ruta, headers=encabezados(C)).status_code == 404, ruta
    assert api.cliente.post(f"/reportes/{otro}/exportar", headers=encabezados(C)).status_code == 404


# --- exportar (RF07) ---------------------------------------------------------------------------------------


def test_exportar_y_descargar_el_xml(api):
    reporte, _ = _consolidado(api)
    assert api.cliente.get(f"/reportes/{reporte['id']}/xml", headers=encabezados(A)).status_code == 404

    r = api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C))
    assert r.status_code == 200, r.text
    exportado = r.json()
    assert exportado["estado"] == "EXPORTADO"
    assert exportado["exportado_por"] == USUARIOS[C].id and exportado["exportado_en"]

    # El auxiliar descarga el archivo para cargarlo en el ERP (RF07 paso 7).
    xml = api.cliente.get(f"/reportes/{reporte['id']}/xml", headers=encabezados(A))
    assert xml.status_code == 200
    assert xml.headers["content-type"].startswith("application/xml")
    assert hashlib.sha256(xml.content).hexdigest() == xml.headers["x-contenido-sha256"] == exportado["archivo_sha256"]
    assert f"reporte-diario-{reporte['fecha_jornada']}" in xml.headers["content-disposition"]
    assert ET.fromstring(xml.content).get("version") == "1.0"

    eventos = api.cliente.get(f"/reportes/{reporte['id']}/eventos", headers=encabezados(A)).json()
    assert [(e["tipo"], e["usuario_id"]) for e in eventos] == [
        ("CONSOLIDADO", USUARIOS[A].id),
        ("EXPORTADO", USUARIOS[C].id),
    ]
    assert eventos[-1]["despues"]["sha256"] == exportado["archivo_sha256"]


def test_exportar_dos_veces_responde_409_y_no_cambia_el_archivo(api):
    reporte, _ = _consolidado(api)
    primero = api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C)).json()
    segundo = api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C))
    assert segundo.status_code == 409 and "no se exporta dos veces" in segundo.json()["detail"]
    xml = api.cliente.get(f"/reportes/{reporte['id']}/xml", headers=encabezados(C))
    assert xml.headers["x-contenido-sha256"] == primero["archivo_sha256"]


def test_si_falla_la_generacion_responde_500_y_el_reporte_sigue_consolidado(api, monkeypatch):
    import agente.application.reportes as reportes

    def falla(*_):
        raise OSError("sin espacio en disco")

    monkeypatch.setattr(reportes, "generar_xml_reporte", falla)
    reporte, _ = _consolidado(api)
    r = api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C))
    assert r.status_code == 500 and "sigue CONSOLIDADO" in r.json()["detail"]
    assert api.cliente.get(f"/reportes/{reporte['id']}", headers=encabezados(C)).json()["estado"] == "CONSOLIDADO"


def test_el_auxiliar_no_exporta_y_el_403_no_cambia_nada(api):
    reporte, _ = _consolidado(api)
    assert api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(A)).status_code == 403
    assert api.reportes.obtener(uuid.UUID(reporte["id"])).estado is EstadoReporte.CONSOLIDADO


def test_una_transaccion_exportada_no_se_corrige_ni_se_rechaza(api):
    reporte, ids = _consolidado(api)
    api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C))
    partidas = [
        {"cuenta": p.cuenta, "naturaleza": p.naturaleza.value, "valor": str(p.valor)}
        for p in partidas_correctas(api.catalogo)
    ]
    corregir = api.cliente.post(
        f"/transacciones/{ids[0]}/corregir",
        json={"partidas": partidas, "motivo": "Corrección después de exportar"},
        headers=encabezados(C),
    )
    assert corregir.status_code == 409 and "EXPORTADO no se puede anular" in corregir.json()["detail"]
    rechazar = api.cliente.post(
        f"/transacciones/{ids[0]}/rechazar", json={"motivo": "Rechazo después de exportar"}, headers=encabezados(C)
    )
    assert rechazar.status_code == 409
    transaccion = api.cliente.get(f"/transacciones/{ids[0]}", headers=encabezados(C)).json()
    assert transaccion["reporte_id"] == reporte["id"] and transaccion["estado"] == "APROBADA_AUTOMATICA"


# --- anular (ampliación del manual) ------------------------------------------------------------------------


def test_anular_un_consolidado_libera_sus_transacciones(api):
    reporte, ids = _consolidado(api)
    r = api.cliente.post(
        f"/reportes/{reporte['id']}/anular", json={"motivo": "Falta corregir FV-1"}, headers=encabezados(C)
    )
    assert r.status_code == 200 and r.json()["estado"] == "ANULADO"
    assert r.json()["transacciones"] == reporte["transacciones"]
    for id_ in ids:
        assert api.cliente.get(f"/transacciones/{id_}", headers=encabezados(C)).json()["reporte_id"] is None
    # Liberadas, se vuelven a consolidar.
    assert _consolidar(api).status_code == 201


def test_anular_exige_motivo_y_no_aplica_a_un_exportado(api):
    reporte, _ = _consolidado(api)
    ruta = f"/reportes/{reporte['id']}/anular"
    assert api.cliente.post(ruta, json={"motivo": "corto"}, headers=encabezados(C)).status_code == 422
    assert api.cliente.post(ruta, json={}, headers=encabezados(C)).status_code == 422
    api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C))
    r = api.cliente.post(ruta, json={"motivo": "Motivo suficiente"}, headers=encabezados(C))
    assert r.status_code == 409 and "nunca se anula" in r.json()["detail"]


def test_el_auxiliar_no_anula(api):
    reporte, _ = _consolidado(api)
    ruta = f"/reportes/{reporte['id']}/anular"
    r = api.cliente.post(ruta, json={"motivo": "Motivo suficiente"}, headers=encabezados(A))
    assert r.status_code == 403
    assert api.reportes.obtener(uuid.UUID(reporte["id"])).estado is EstadoReporte.CONSOLIDADO
