"""API de notificaciones (RF04) con repositorios en memoria.

Los 401 (sin token) y 403 (rol sin permiso) de cada ruta están en test_autenticacion.py y test_permisos.py.
"""

import pytest
from autenticacion_pruebas import USUARIOS, encabezados
from fabricas import resultado_alerta_con_asiento

from agente.domain.enums import Rol, TipoNotificacion
from agente.domain.notificaciones import Causa

C, A, ADM = Rol.CONTADOR, Rol.AUXILIAR, Rol.ADMIN
T = TipoNotificacion


def _registrar(api, tipo=T.ARCHIVO_NO_RECIBIDO, clave="archivo:a.xml", mensaje="No se recibió a.xml"):
    return api.notificaciones.registrar(Causa(tipo, clave, mensaje, {"archivo": "a.xml"}))


def _listar(api, rol, consulta=""):
    r = api.cliente.get(f"/notificaciones{consulta}", headers=encabezados(rol))
    assert r.status_code == 200, r.text
    return r.json()


def test_cada_usuario_ve_solo_las_suyas(entorno_api):
    tecnica = _registrar(entorno_api)
    contable = _registrar(entorno_api, T.TRANSACCION_EN_ALERTA, "transaccion:1", "FV-1 en alerta")
    assert [n["id"] for n in _listar(entorno_api, ADM)] == [tecnica]
    assert [n["id"] for n in _listar(entorno_api, C)] == [contable]
    (auxiliar,) = _listar(entorno_api, A)
    assert (auxiliar["id"], auxiliar["informativa"], auxiliar["clase"]) == (contable, True, "CONTABLE")
    (admin,) = _listar(entorno_api, ADM)
    assert admin["tipo"] == "ARCHIVO_NO_RECIBIDO" and admin["clase"] == "TECNICA"
    assert admin["se_atiende_a_mano"] is True and admin["datos"] == {"archivo": "a.xml"}
    assert admin["leida_en"] is None and admin["atendida_en"] is None


def test_contar_leer_una_y_leer_todas(entorno_api):
    uno = _registrar(entorno_api, clave="archivo:1.xml")
    dos = _registrar(entorno_api, clave="archivo:2.xml")
    total = "/notificaciones/no-leidas/total"
    assert entorno_api.cliente.get(total, headers=encabezados(ADM)).json() == {"no_leidas": 2}
    leida = entorno_api.cliente.post(f"/notificaciones/{uno}/leer", headers=encabezados(ADM))
    assert leida.status_code == 200 and leida.json()["leida_en"]
    assert [n["id"] for n in _listar(entorno_api, ADM, "?no_leidas=true")] == [dos]
    r = entorno_api.cliente.post("/notificaciones/leer-todas", headers=encabezados(ADM))
    assert r.json() == {"marcadas": 1}
    assert entorno_api.cliente.get(total, headers=encabezados(ADM)).json() == {"no_leidas": 0}


@pytest.mark.parametrize("accion", ["leer", "atender"])
def test_la_notificacion_de_otro_usuario_responde_404(entorno_api, accion):
    tecnica = _registrar(entorno_api)
    for rol in (C, A):
        r = entorno_api.cliente.post(f"/notificaciones/{tecnica}/{accion}", headers=encabezados(rol))
        assert r.status_code == 404, rol
    assert entorno_api.cliente.post(f"/notificaciones/999/{accion}", headers=encabezados(ADM)).status_code == 404
    assert _listar(entorno_api, ADM)[0]["leida_en"] is None


def test_el_admin_atiende_a_mano_una_tecnica(entorno_api):
    id_ = _registrar(entorno_api)
    r = entorno_api.cliente.post(f"/notificaciones/{id_}/atender", headers=encabezados(ADM))
    assert r.status_code == 200, r.text
    assert r.json()["atendida_por"] == USUARIOS[ADM].id and r.json()["atendida_en"]
    otra_vez = entorno_api.cliente.post(f"/notificaciones/{id_}/atender", headers=encabezados(ADM))
    assert otra_vez.status_code == 409 and "ya está atendida" in otra_vez.json()["detail"]
    assert _listar(entorno_api, ADM, "?no_atendidas=true") == []


def test_el_auxiliar_atiende_a_mano_una_factura_duplicada(entorno_api):
    id_ = _registrar(entorno_api, T.FACTURA_DUPLICADA, "archivo:dup.xml", "FV-1 enviada dos veces")
    assert entorno_api.cliente.post(f"/notificaciones/{id_}/atender", headers=encabezados(A)).status_code == 200


@pytest.mark.parametrize(
    ("tipo", "rol"),
    [
        (T.TRANSACCION_EN_ALERTA, C),
        (T.TRANSACCION_EN_ALERTA, A),
        (T.CONSOLIDACION_BLOQUEADA, C),
        (T.REPORTE_DISPONIBLE, C),
        (T.REPORTE_DISPONIBLE, A),
        (T.JORNADA_SIN_CIERRE, ADM),
        (T.EXPORTACION_FALLIDA, ADM),
        (T.DIRECTORIO_NO_DISPONIBLE, ADM),
    ],
)
def test_las_que_se_resuelven_solas_responden_409_al_atenderlas_a_mano(entorno_api, tipo, rol):
    id_ = _registrar(entorno_api, tipo, "causa:1", "x")
    r = entorno_api.cliente.post(f"/notificaciones/{id_}/atender", headers=encabezados(rol))
    assert r.status_code == 409 and "no se atiende a mano" in r.json()["detail"]
    assert _listar(entorno_api, rol)[0]["atendida_en"] is None


def test_id_no_numerico_responde_422(entorno_api):
    assert entorno_api.cliente.post("/notificaciones/abc/leer", headers=encabezados(ADM)).status_code == 422


# --- Transacción en alerta (RF04 y RN05) -------------------------------------------------------------------


def test_factura_en_alerta_el_auxiliar_la_ve_no_la_aprueba_y_al_resolverla_queda_atendida(entorno_api, catalogo):
    id_ = entorno_api.transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-ALERTA-1"))
    (contador,) = _listar(entorno_api, C)
    (auxiliar,) = _listar(entorno_api, A)
    assert contador["id"] == auxiliar["id"] and contador["transaccion_id"] == str(id_)
    assert (contador["informativa"], auxiliar["informativa"]) == (False, True)
    assert contador["datos"]["numero_factura"] == "FV-ALERTA-1"

    assert entorno_api.cliente.post(f"/transacciones/{id_}/aprobar", headers=encabezados(A)).status_code == 403
    r = entorno_api.cliente.post(f"/notificaciones/{auxiliar['id']}/atender", headers=encabezados(A))
    assert r.status_code == 409
    r = entorno_api.cliente.post(f"/notificaciones/{contador['id']}/atender", headers=encabezados(C))
    assert r.status_code == 409 and "no se atiende a mano" in r.json()["detail"]

    assert entorno_api.cliente.post(f"/transacciones/{id_}/aprobar", headers=encabezados(C)).status_code == 200
    for rol in (C, A):
        (vista,) = _listar(entorno_api, rol)
        assert vista["atendida_en"] and "La transacción se aprobó" in vista["resolucion"]
