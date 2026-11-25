"""Matriz de permisos rol × endpoint (403 para el rol sin permiso).

ESPERADO se escribe a partir de la especificación, no se deriva de agente.api.permisos: si alguien
cambia un permiso en el código sin cambiar esta tabla, la prueba falla.
"""

import pytest
from autenticacion_pruebas import encabezados, ruta_concreta
from fabricas import XML_BASE
from fastapi.routing import APIRoute

from agente.api.main import app
from agente.api.permisos import PERMISO_POR_RUTA, RUTAS_PUBLICAS
from agente.domain.enums import Rol

C, A, ADM = Rol.CONTADOR, Rol.AUXILIAR, Rol.ADMIN
LECTURA = {C, A}

ESPERADO: dict[tuple[str, str], set[Rol]] = {
    # Cargar facturas: solo AUXILIAR.
    ("POST", "/facturas/clasificar"): {A},
    ("POST", "/carpeta/procesar"): {A},
    # Lectura: CONTADOR y AUXILIAR (el ADMIN no lee transacciones ni reglas).
    ("GET", "/transacciones"): LECTURA,
    ("GET", "/transacciones/{id_transaccion}"): LECTURA,
    ("GET", "/transacciones/{id_transaccion}/eventos"): LECTURA,
    ("GET", "/transacciones/{id_transaccion}/documento"): LECTURA,
    ("GET", "/catalogo/cuentas"): LECTURA,
    ("GET", "/reglas"): LECTURA,
    ("GET", "/reglas/{codigo}"): LECTURA,
    ("GET", "/reglas/{codigo}/historial"): LECTURA,
    # Resolver transacciones: solo CONTADOR.
    ("POST", "/transacciones/{id_transaccion}/aprobar"): {C},
    ("POST", "/transacciones/{id_transaccion}/corregir"): {C},
    ("POST", "/transacciones/{id_transaccion}/rechazar"): {C},
    # Crear o editar cuentas y reglas: solo CONTADOR.
    ("POST", "/catalogo/cuentas"): {C},
    ("POST", "/reglas"): {C},
    ("PUT", "/reglas/{codigo}"): {C},
    ("POST", "/reglas/{codigo}/activar"): {C},
    ("POST", "/reglas/{codigo}/desactivar"): {C},
    # Reportes diarios: consolidan el AUXILIAR y el CONTADOR; solo el CONTADOR exporta y anula.
    # La consulta y la descarga del XML son lectura (el auxiliar carga el XML en el ERP, RF07 paso 7).
    ("POST", "/reportes/consolidar"): {C, A},
    ("GET", "/reportes"): LECTURA,
    ("GET", "/reportes/jornada"): LECTURA,
    # Horario de la jornada: lo consultan el CONTADOR y el AUXILIAR; solo el CONTADOR lo cambia.
    ("GET", "/reportes/horario"): {C, A},
    ("GET", "/reportes/horario/historial"): {C, A},
    ("PUT", "/reportes/horario"): {C},
    ("GET", "/reportes/{id_reporte}"): LECTURA,
    ("GET", "/reportes/{id_reporte}/eventos"): LECTURA,
    ("GET", "/reportes/{id_reporte}/xml"): LECTURA,
    ("POST", "/reportes/{id_reporte}/exportar"): {C},
    ("POST", "/reportes/{id_reporte}/anular"): {C},
    # Usuarios: solo ADMIN.
    ("GET", "/usuarios"): {ADM},
    ("POST", "/usuarios"): {ADM},
    ("PUT", "/usuarios/{id_usuario}"): {ADM},
}

# Cuerpos válidos o casi válidos: lo que importa es que el rol permitido NO reciba 401/403.
CUERPOS = {
    ("POST", "/carpeta/procesar"): {"json": {"usar_nlp": False}},
    ("POST", "/transacciones/{id_transaccion}/corregir"): {"json": {"partidas": [], "motivo": "Motivo de prueba"}},
    ("POST", "/transacciones/{id_transaccion}/rechazar"): {"json": {"motivo": "Motivo de prueba"}},
    ("POST", "/catalogo/cuentas"): {"json": {"codigo": "4199", "nombre": "Otros", "naturaleza": "CREDITO"}},
    ("POST", "/reglas"): {"json": {"codigo": "R-NUEVA", "cuenta_ingreso": "4135", "palabras_clave": ["abarrote"]}},
    ("PUT", "/reglas/{codigo}"): {"json": {"cuenta_ingreso": "4135", "palabras_clave": ["mercancia"]}},
    ("POST", "/usuarios"): {
        "json": {
            "nombre_usuario": "nuevo",
            "nombre_completo": "Nuevo",
            "contrasena": "clave-segura-1",
            "rol": "AUXILIAR",
        }
    },
    ("PUT", "/usuarios/{id_usuario}"): {"json": {"nombre_completo": "Otro nombre"}},
    ("POST", "/reportes/{id_reporte}/anular"): {"json": {"motivo": "Motivo de prueba"}},
    ("PUT", "/reportes/horario"): {"json": {"hora_apertura": "08:00", "hora_cierre": "18:00"}},
}


def _peticion(cliente, metodo, ruta, rol):
    opciones = dict(CUERPOS.get((metodo, ruta), {}))
    if (metodo, ruta) == ("POST", "/facturas/clasificar"):
        opciones = {
            "files": {"archivo": ("f.xml", XML_BASE.encode(), "application/xml")},
            "params": {"usar_nlp": "false", "guardar": "false"},
        }
    return cliente.request(metodo, ruta_concreta(ruta), headers=encabezados(rol), **opciones)


def test_la_matriz_cubre_exactamente_las_rutas_de_la_api():
    rutas = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
    assert rutas - RUTAS_PUBLICAS == set(ESPERADO)
    assert set(PERMISO_POR_RUTA) == set(ESPERADO)


@pytest.mark.parametrize("rol", list(Rol))
@pytest.mark.parametrize(("metodo", "ruta"), sorted(ESPERADO))
def test_matriz_de_permisos(entorno_api, rol, metodo, ruta):
    r = _peticion(entorno_api.cliente, metodo, ruta, rol)
    if rol in ESPERADO[(metodo, ruta)]:
        assert r.status_code not in (401, 403), f"{rol} debería poder {metodo} {ruta}: {r.status_code} {r.text}"
    else:
        assert r.status_code == 403, f"{rol} NO debería poder {metodo} {ruta}: {r.status_code}"
        assert rol.value in r.json()["detail"]


def test_el_403_no_ejecuta_la_accion(entorno_api):
    # El AUXILIAR no puede desactivar reglas: la regla sigue activa y no hay historial.
    r = entorno_api.cliente.post("/reglas/R-4135-COMERCIO/desactivar", headers=encabezados(A))
    assert r.status_code == 403
    assert entorno_api.reglas.obtener_regla("R-4135-COMERCIO").activa is True
    assert entorno_api.reglas.historial("R-4135-COMERCIO") == []
