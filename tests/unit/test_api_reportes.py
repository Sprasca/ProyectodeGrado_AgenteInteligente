"""API de reportes diarios (RF06, RF07): el ciclo completo con repositorios en memoria.

Los 401 (sin token) y 403 (rol sin permiso) de cada ruta están en test_autenticacion.py y test_permisos.py.
"""

import hashlib
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, time, timedelta

import pytest
from autenticacion_pruebas import USUARIOS, encabezados
from fabricas import partidas_correctas, resultado_alerta_con_asiento, resultado_aprobado

from agente.domain.enums import EstadoReporte, Rol
from agente.domain.jornada import ZONA_COLOMBIA

C, A = Rol.CONTADOR, Rol.AUXILIAR


@pytest.fixture
def api(entorno_api, catalogo):
    entorno_api.catalogo = catalogo
    return entorno_api


def _aprobadas(api, *numeros):
    return [api.transacciones.guardar(resultado_aprobado(api.catalogo, n)) for n in numeros]


def _consolidar(api, rol=A, **cuerpo):
    return api.cliente.post("/reportes/consolidar", json=cuerpo or None, headers=encabezados(rol))


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
    assert (reporte["estado"], reporte["tipo"], reporte["numero"]) == ("CONSOLIDADO", "PARCIAL", 1)
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
    assert f"reporte-diario-{reporte['fecha_jornada']}-1-parcial" in xml.headers["content-disposition"]
    assert ET.fromstring(xml.content).get("version") == "1.0"

    eventos = api.cliente.get(f"/reportes/{reporte['id']}/eventos", headers=encabezados(A)).json()
    assert [(e["tipo"], e["usuario_id"]) for e in eventos] == [
        ("CONSOLIDADO", USUARIOS[A].id),
        ("EXPORTADO", USUARIOS[C].id),
    ]
    assert eventos[-1]["despues"]["sha256"] == exportado["archivo_sha256"]
    for id_ in reporte["transacciones"]:
        ultimo = api.cliente.get(f"/transacciones/{id_}/eventos", headers=encabezados(A)).json()[-1]
        assert (ultimo["tipo"], ultimo["usuario_id"]) == ("EXPORTADA", USUARIOS[C].id)


def test_exportar_dos_veces_responde_409_y_no_cambia_el_archivo(api):
    reporte, _ = _consolidado(api)
    primero = api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C)).json()
    segundo = api.cliente.post(f"/reportes/{reporte['id']}/exportar", headers=encabezados(C))
    assert segundo.status_code == 409 and "no se exporta dos veces" in segundo.json()["detail"]
    xml = api.cliente.get(f"/reportes/{reporte['id']}/xml", headers=encabezados(C))
    assert xml.headers["x-contenido-sha256"] == primero["archivo_sha256"]


def test_si_falla_la_generacion_responde_500_y_el_reporte_sigue_consolidado(api, monkeypatch):
    from agente.infrastructure.erp import xml_reporte

    def falla(*_):
        raise OSError("sin espacio en disco")

    monkeypatch.setattr(xml_reporte, "generar_xml_reporte", falla)
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


# --- Parciales, cierre y límite por jornada ------------------------------------------------------------------

AYER = datetime.now(ZONA_COLOMBIA).date() - timedelta(days=1)


def _recibir_ayer(api, numero, hora, minuto=0):
    """Recibe una factura ayer a esa hora de Colombia (la jornada de ayer ya terminó)."""
    api.transacciones.reloj = lambda: datetime.combine(AYER, time(hora, minuto), tzinfo=ZONA_COLOMBIA)
    try:
        return api.transacciones.guardar(resultado_aprobado(api.catalogo, numero))
    finally:
        api.transacciones.reloj = lambda: datetime.now(UTC)


def test_cierre_con_corte_a_la_hora_de_cierre_y_la_factura_tardia_queda_para_la_jornada_siguiente(api):
    a_tiempo = _recibir_ayer(api, "FV-1759", 17, 59)
    tardia = _recibir_ayer(api, "FV-1805", 18, 5)
    r = _consolidar(api, tipo="CIERRE", fecha_jornada=AYER.isoformat())
    assert r.status_code == 201, r.text
    cierre = r.json()
    assert (cierre["tipo"], cierre["numero"], cierre["fecha_jornada"]) == ("CIERRE", 1, AYER.isoformat())
    assert cierre["transacciones"] == [str(a_tiempo)]
    assert datetime.fromisoformat(cierre["corte"]) == datetime.combine(AYER, time(18, 0), tzinfo=ZONA_COLOMBIA)
    tardia_json = api.cliente.get(f"/transacciones/{tardia}", headers=encabezados(C)).json()
    assert tardia_json["fecha_jornada"] == (AYER + timedelta(days=1)).isoformat()
    assert tardia_json["reporte_id"] is None


def test_un_segundo_cierre_responde_409(api):
    _recibir_ayer(api, "FV-1", 10)
    assert _consolidar(api, tipo="CIERRE", fecha_jornada=AYER.isoformat()).status_code == 201
    _recibir_ayer(api, "FV-2", 11)
    segundo = _consolidar(api, C, tipo="CIERRE", fecha_jornada=AYER.isoformat())
    assert segundo.status_code == 409 and "ya tiene el reporte de cierre" in segundo.json()["detail"]


def test_el_cierre_de_la_jornada_en_curso_antes_de_la_hora_responde_409(api):
    en_curso = api.cliente.get("/reportes/jornada", headers=encabezados(A)).json()["fecha_jornada"]
    r = _consolidar(api, tipo="CIERRE", fecha_jornada=en_curso)
    assert r.status_code == 409 and "aún no termina" in r.json()["detail"]


def test_un_parcial_de_una_jornada_terminada_responde_409(api):
    _recibir_ayer(api, "FV-1", 10)
    r = _consolidar(api, tipo="PARCIAL", fecha_jornada=AYER.isoformat())
    assert r.status_code == 409 and "jornada en curso" in r.json()["detail"]


def test_el_quinto_parcial_responde_409_y_el_cierre_sigue_siendo_posible(api):
    for n in range(4):
        _aprobadas(api, f"FV-P{n}")
        assert _consolidar(api).status_code == 201
    _aprobadas(api, "FV-P5")
    quinto = _consolidar(api)
    assert quinto.status_code == 409 and "quinto cupo es del reporte de cierre" in quinto.json()["detail"]
    estado = api.cliente.get("/reportes/jornada", headers=encabezados(A)).json()
    assert (estado["parciales_vigentes"], estado["puede_consolidar_parcial"]) == (4, False)


def test_tipo_desconocido_responde_422(api):
    assert _consolidar(api, tipo="AUTORIZADO").status_code == 422
    assert _consolidar(api, otro="x").status_code == 422


def test_estado_de_la_jornada(api):
    _aprobadas(api, "FV-1")
    estado = api.cliente.get("/reportes/jornada", headers=encabezados(A)).json()
    assert (estado["en_curso"], estado["hora_apertura"], estado["hora_cierre"]) == (True, "08:00:00", "18:00:00")
    assert (estado["reportes_vigentes"], estado["maximo_reportes"], estado["tiene_cierre"]) == (0, 5, False)
    assert (estado["alertas_pendientes"], estado["transacciones_sin_reporte"]) == (0, 1)
    assert estado["puede_consolidar"] is True and estado["puede_cerrar"] is False
    api.transacciones.guardar(resultado_alerta_con_asiento(api.catalogo, "FV-A"))
    estado = api.cliente.get("/reportes/jornada", headers=encabezados(C)).json()
    assert (estado["alertas_pendientes"], estado["puede_consolidar"]) == (1, False)
    ayer = api.cliente.get(f"/reportes/jornada?fecha={AYER.isoformat()}", headers=encabezados(A)).json()
    assert (ayer["fecha_jornada"], ayer["en_curso"], ayer["terminada"]) == (AYER.isoformat(), False, True)


def test_listar_por_jornada(api):
    reporte, _ = _consolidado(api)
    fecha = reporte["fecha_jornada"]
    assert [r["id"] for r in api.cliente.get(f"/reportes?fecha_jornada={fecha}", headers=encabezados(A)).json()] == [
        reporte["id"]
    ]
    assert api.cliente.get(f"/reportes?fecha_jornada={AYER.isoformat()}", headers=encabezados(A)).json() == []


# --- Horario de la jornada -------------------------------------------------------------------------------


def test_consultar_el_horario_inicial(api):
    for rol in (A, C):
        horario = api.cliente.get("/reportes/horario", headers=encabezados(rol)).json()
        assert horario == {
            "hora_apertura": "08:00",
            "hora_cierre": "18:00",
            "zona_horaria": "America/Bogota",
            "actualizado_por": None,
            "actualizado_en": None,
        }


def test_el_contador_cambia_el_horario_y_queda_quien_y_cuando(api):
    cierre = api.horario.vigente().hora_cierre
    r = api.cliente.put(
        "/reportes/horario",
        json={"hora_apertura": "07:30", "hora_cierre": f"{cierre:%H:%M}"},
        headers=encabezados(C),
    )
    assert r.status_code == 200, r.text
    assert (r.json()["hora_apertura"], r.json()["actualizado_por"]) == ("07:30", USUARIOS[C].id)
    assert r.json()["actualizado_en"]
    historial = api.cliente.get("/reportes/horario/historial", headers=encabezados(A)).json()
    assert [h["hora_apertura"] for h in historial] == ["07:30", "08:00"]


@pytest.mark.parametrize(
    ("cuerpo", "detalle"),
    [
        ({"hora_apertura": "18:00", "hora_cierre": "08:00"}, "anterior a la de cierre"),
        ({"hora_apertura": "8", "hora_cierre": "18:00"}, "HH:MM"),
        ({"hora_apertura": "08:00", "hora_cierre": "25:00"}, "HH:MM"),
    ],
)
def test_horario_invalido_responde_422(api, cuerpo, detalle):
    r = api.cliente.put("/reportes/horario", json=cuerpo, headers=encabezados(C))
    assert r.status_code == 422 and detalle in r.json()["detail"]
    assert api.horario.vigente().hora_apertura == time(8, 0)


def test_un_cambio_que_moveria_la_jornada_en_curso_responde_409(api):
    ahora = datetime.now(ZONA_COLOMBIA)
    # Si aún no es la hora de cierre, un cierre que ya pasó hoy movería la jornada; si ya pasó, uno posterior.
    if ahora.time() < api.horario.vigente().hora_cierre:
        cuerpo = {"hora_apertura": "00:00", "hora_cierre": "00:01"}
    else:
        cuerpo = {"hora_apertura": "00:00", "hora_cierre": "23:59"}
    if ahora.time() < time(0, 2) or ahora.time() >= time(23, 59):
        pytest.skip("a esta hora el cambio no movería la jornada")
    r = api.cliente.put("/reportes/horario", json=cuerpo, headers=encabezados(C))
    assert r.status_code == 409 and "movería la jornada en curso" in r.json()["detail"]


def test_el_auxiliar_no_cambia_el_horario(api):
    r = api.cliente.put(
        "/reportes/horario", json={"hora_apertura": "07:00", "hora_cierre": "18:00"}, headers=encabezados(A)
    )
    assert r.status_code == 403
    assert len(api.horario.historial()) == 1
