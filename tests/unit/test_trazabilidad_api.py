"""Trazabilidad por la API: eventos con su usuario, rechazo y motivo, documento original y CUFE."""

import hashlib
import uuid
from io import BytesIO

import pytest
from autenticacion_pruebas import USUARIOS, encabezados
from fabricas import (
    cufe_unico,
    resultado_alerta_con_asiento,
    resultado_alerta_sin_asiento,
    resultado_aprobado,
    xml_factura,
)
from reportlab.pdfgen import canvas

from agente.domain.enums import Rol

CONTADOR, AUXILIAR = encabezados(Rol.CONTADOR), encabezados(Rol.AUXILIAR)
ID_CONTADORA, ID_AUXILIAR = USUARIOS[Rol.CONTADOR].id, USUARIOS[Rol.AUXILIAR].id
MOTIVO = "Revisado contra el soporte físico"


def _subir(entorno, contenido=None, nombre="factura.xml", **params):
    return entorno.cliente.post(
        "/facturas/clasificar",
        files={"archivo": (nombre, contenido if contenido is not None else xml_factura(), "application/octet-stream")},
        params={"usar_nlp": "false", **params},
        headers=AUXILIAR,
    )


def _eventos(entorno, id_):
    r = entorno.cliente.get(f"/transacciones/{id_}/eventos", headers=CONTADOR)
    assert r.status_code == 200
    return r.json()


def _cuerpo_correccion(catalogo):
    return {
        "partidas": [
            {"cuenta": catalogo.resolver("caja"), "naturaleza": "DEBITO", "valor": "119000"},
            {"cuenta": "4135", "naturaleza": "CREDITO", "valor": "100000"},
            {"cuenta": catalogo.cuenta_iva(), "naturaleza": "CREDITO", "valor": "19000"},
        ],
        "motivo": MOTIVO,
    }


# --- Eventos de cada acción con su usuario ----------------------------------------------------


def test_cargar_registra_recibida_del_auxiliar_y_clasificada_automatica(entorno_api):
    id_ = _subir(entorno_api).json()["id"]
    recibida, clasificada = _eventos(entorno_api, id_)
    assert (recibida["tipo"], recibida["usuario_id"]) == ("RECIBIDA", ID_AUXILIAR)
    assert recibida["despues"]["documento"]["nombre_archivo"] == "factura.xml"
    assert (clasificada["tipo"], clasificada["usuario_id"]) == ("CLASIFICADA", None)
    assert clasificada["despues"]["estado"] == "APROBADA_AUTOMATICA"


def test_aprobar_registra_evento_del_contador_con_motivo_opcional(entorno_api, catalogo):
    con_motivo = entorno_api.transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-1"))
    sin_motivo = entorno_api.transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-2"))

    r = entorno_api.cliente.post(f"/transacciones/{con_motivo}/aprobar", json={"motivo": MOTIVO}, headers=CONTADOR)
    assert r.status_code == 200
    assert entorno_api.cliente.post(f"/transacciones/{sin_motivo}/aprobar", headers=CONTADOR).status_code == 200

    evento = _eventos(entorno_api, con_motivo)[-1]
    assert (evento["tipo"], evento["usuario_id"], evento["motivo"]) == ("APROBADA", ID_CONTADORA, MOTIVO)
    assert _eventos(entorno_api, sin_motivo)[-1]["motivo"] is None


def test_corregir_registra_evento_del_contador(entorno_api, catalogo):
    id_ = entorno_api.transacciones.guardar(resultado_alerta_sin_asiento(catalogo, "FV-1"))
    r = entorno_api.cliente.post(f"/transacciones/{id_}/corregir", json=_cuerpo_correccion(catalogo), headers=CONTADOR)
    assert r.status_code == 200
    evento = _eventos(entorno_api, id_)[-1]
    assert (evento["tipo"], evento["usuario_id"], evento["motivo"]) == ("CORREGIDA", ID_CONTADORA, MOTIVO)
    assert evento["antes"] == {"estado": "EN_ALERTA", "partidas": []}
    assert [p["cuenta"] for p in evento["despues"]["partidas"]] == [
        catalogo.resolver("caja"),
        "4135",
        catalogo.cuenta_iva(),
    ]


def test_eventos_de_transaccion_inexistente_404(entorno_api):
    assert entorno_api.cliente.get(f"/transacciones/{uuid.uuid4()}/eventos", headers=CONTADOR).status_code == 404


def test_historial_de_reglas_registra_al_usuario_autenticado(entorno_api):
    cliente = entorno_api.cliente
    nueva = {"codigo": "R-NUEVA", "cuenta_ingreso": "4135", "palabras_clave": ["abarrote fino"]}
    assert cliente.post("/reglas", json=nueva, headers=CONTADOR).status_code == 201
    edicion = {"cuenta_ingreso": "4135", "palabras_clave": ["abarrote fino", "granos"]}
    assert cliente.put("/reglas/R-NUEVA", json=edicion, headers=CONTADOR).status_code == 200
    assert cliente.post("/reglas/R-NUEVA/desactivar", headers=CONTADOR).status_code == 200
    assert cliente.post("/reglas/R-NUEVA/activar", headers=CONTADOR).status_code == 200

    historial = cliente.get("/reglas/R-NUEVA/historial", headers=AUXILIAR).json()
    assert [(e["accion"], e["usuario"]) for e in historial] == [
        ("CREAR", "contadora"),
        ("EDITAR", "contadora"),
        ("DESACTIVAR", "contadora"),
        ("ACTIVAR", "contadora"),
    ]


# --- Rechazo y motivo obligatorio -------------------------------------------------------------


@pytest.mark.parametrize("fabrica", [resultado_alerta_con_asiento, resultado_aprobado])
def test_rechazar_desde_en_alerta_o_aprobada_automatica(entorno_api, catalogo, fabrica):
    id_ = entorno_api.transacciones.guardar(fabrica(catalogo, "FV-1"))
    r = entorno_api.cliente.post(f"/transacciones/{id_}/rechazar", json={"motivo": MOTIVO}, headers=CONTADOR)
    assert r.status_code == 200
    assert r.json()["estado"] == "RECHAZADA"
    evento = _eventos(entorno_api, id_)[-1]
    assert (evento["tipo"], evento["usuario_id"], evento["motivo"]) == ("RECHAZADA", ID_CONTADORA, MOTIVO)


def test_rechazada_es_final(entorno_api, catalogo):
    id_ = entorno_api.transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-1"))
    cliente = entorno_api.cliente
    assert cliente.post(f"/transacciones/{id_}/rechazar", json={"motivo": MOTIVO}, headers=CONTADOR).status_code == 200
    assert cliente.post(f"/transacciones/{id_}/aprobar", headers=CONTADOR).status_code == 409
    cuerpo = _cuerpo_correccion(catalogo)
    assert cliente.post(f"/transacciones/{id_}/corregir", json=cuerpo, headers=CONTADOR).status_code == 409
    assert cliente.post(f"/transacciones/{id_}/rechazar", json={"motivo": MOTIVO}, headers=CONTADOR).status_code == 409
    assert cliente.get(f"/transacciones/{id_}", headers=CONTADOR).json()["estado"] == "RECHAZADA"


def test_no_se_rechaza_lo_ya_aprobado_por_el_contador(entorno_api, catalogo):
    id_ = entorno_api.transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-1"))
    entorno_api.transacciones.aprobar(id_)
    r = entorno_api.cliente.post(f"/transacciones/{id_}/rechazar", json={"motivo": MOTIVO}, headers=CONTADOR)
    assert r.status_code == 409


def test_rechazar_inexistente_404(entorno_api):
    r = entorno_api.cliente.post(f"/transacciones/{uuid.uuid4()}/rechazar", json={"motivo": MOTIVO}, headers=CONTADOR)
    assert r.status_code == 404


@pytest.mark.parametrize("cuerpo", [{}, {"motivo": ""}, {"motivo": "corto"}, {"motivo": "   123456789   "}])
def test_rechazar_sin_motivo_suficiente_422(entorno_api, catalogo, cuerpo):
    id_ = entorno_api.transacciones.guardar(resultado_alerta_con_asiento(catalogo, "FV-1"))
    r = entorno_api.cliente.post(f"/transacciones/{id_}/rechazar", json=cuerpo, headers=CONTADOR)
    assert r.status_code == 422
    assert entorno_api.transacciones.obtener(id_).estado.value == "EN_ALERTA"
    assert len(_eventos(entorno_api, id_)) == 2


@pytest.mark.parametrize("motivo", [None, "", "corto"])
def test_corregir_sin_motivo_suficiente_422(entorno_api, catalogo, motivo):
    id_ = entorno_api.transacciones.guardar(resultado_alerta_sin_asiento(catalogo, "FV-1"))
    cuerpo = _cuerpo_correccion(catalogo)
    if motivo is None:
        del cuerpo["motivo"]
    else:
        cuerpo["motivo"] = motivo
    r = entorno_api.cliente.post(f"/transacciones/{id_}/corregir", json=cuerpo, headers=CONTADOR)
    assert r.status_code == 422
    assert entorno_api.transacciones.obtener(id_).estado.value == "EN_ALERTA"


# --- Documento original ----------------------------------------------------------------------


def test_descargar_documento_xml_identico_con_su_sha256(entorno_api):
    contenido = xml_factura("FV-77")
    id_ = _subir(entorno_api, contenido, nombre="Factura FV-77.xml").json()["id"]
    r = entorno_api.cliente.get(f"/transacciones/{id_}/documento", headers=AUXILIAR)
    assert r.status_code == 200
    assert r.content == contenido
    assert r.headers["x-contenido-sha256"] == hashlib.sha256(contenido).hexdigest()
    assert hashlib.sha256(r.content).hexdigest() == r.headers["x-contenido-sha256"]
    assert r.headers["content-type"].startswith("application/xml")
    assert 'filename="Factura FV-77.xml"' in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"


def _pdf_con_texto() -> bytes:
    salida = BytesIO()
    c = canvas.Canvas(salida)
    for i, linea in enumerate(["Factura No: FV-2001", "Fecha: 2025-03-15", "Total a pagar: $ 119.000,00"]):
        c.drawString(72, 800 - 20 * i, linea)
    c.showPage()
    c.save()
    return salida.getvalue()


def test_descargar_documento_pdf(entorno_api):
    contenido = _pdf_con_texto()
    id_ = _subir(entorno_api, contenido, nombre="factura.pdf").json()["id"]
    r = entorno_api.cliente.get(f"/transacciones/{id_}/documento", headers=CONTADOR)
    assert r.status_code == 200
    assert r.content == contenido
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-contenido-sha256"] == hashlib.sha256(contenido).hexdigest()


def test_el_nombre_del_archivo_se_guarda_sin_rutas(entorno_api):
    id_ = _subir(entorno_api, nombre="..\\..\\Windows\\factúra.xml").json()["id"]
    r = entorno_api.cliente.get(f"/transacciones/{id_}/documento", headers=CONTADOR)
    assert "filename*=UTF-8''fact%C3%BAra.xml" in r.headers["content-disposition"]
    assert ".." not in r.headers["content-disposition"]


def test_documento_inexistente_404(entorno_api, catalogo):
    sin_documento = entorno_api.transacciones.guardar(resultado_aprobado(catalogo, "FV-1"))
    assert entorno_api.cliente.get(f"/transacciones/{sin_documento}/documento", headers=CONTADOR).status_code == 404
    assert entorno_api.cliente.get(f"/transacciones/{uuid.uuid4()}/documento", headers=CONTADOR).status_code == 404


def test_sin_guardar_no_queda_documento(entorno_api):
    assert "id" not in _subir(entorno_api, guardar="false").json()
    assert entorno_api.transacciones.listar() == []


# --- CUFE ------------------------------------------------------------------------------------


def test_cufe_se_guarda_y_uno_repetido_es_409(entorno_api):
    cufe = cufe_unico()
    primera = _subir(entorno_api, xml_factura("FV-100", cufe))
    assert primera.status_code == 200
    assert primera.json()["factura"]["cufe"] == cufe
    guardada = entorno_api.cliente.get(f"/transacciones/{primera.json()['id']}", headers=CONTADOR).json()
    assert guardada["cufe"] == cufe

    # Mismo CUFE con otro número: es la misma factura.
    repetida = _subir(entorno_api, xml_factura("FV-OTRO", cufe))
    assert repetida.status_code == 409
    assert cufe in repetida.json()["detail"]
    assert len(entorno_api.transacciones.listar()) == 1


def test_numero_repetido_con_otro_cufe_tambien_es_409(entorno_api):
    assert _subir(entorno_api, xml_factura("FV-100", cufe_unico())).status_code == 200
    assert _subir(entorno_api, xml_factura("FV-100", cufe_unico())).status_code == 409
