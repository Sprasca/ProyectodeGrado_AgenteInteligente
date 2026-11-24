"""Permisos de la API: ÚNICO lugar donde se decide qué rol puede usar cada ruta.

- PERMISOS_POR_ROL: qué puede hacer cada rol.
- PERMISO_POR_RUTA: qué permiso exige cada (método, ruta).
Una ruta protegida que no esté en PERMISO_POR_RUTA queda negada para todos (403).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ..domain.enums import Rol
from ..domain.usuarios import Usuario
from .autenticacion import usuario_actual


class Permiso(StrEnum):
    LEER = "LEER"  # consultar transacciones, eventos, documentos, cuentas y reglas
    CARGAR_FACTURAS = "CARGAR_FACTURAS"
    RESOLVER_TRANSACCIONES = "RESOLVER_TRANSACCIONES"  # aprobar, corregir, rechazar
    ADMINISTRAR_CATALOGO = "ADMINISTRAR_CATALOGO"  # crear cuentas; crear, editar, activar y desactivar reglas
    ADMINISTRAR_USUARIOS = "ADMINISTRAR_USUARIOS"
    CONSOLIDAR_REPORTES = "CONSOLIDAR_REPORTES"  # cerrar la jornada (RF06)
    EXPORTAR_REPORTES = "EXPORTAR_REPORTES"  # autorizar y exportar en un solo paso (RF07)
    ANULAR_REPORTES = "ANULAR_REPORTES"  # anular un reporte CONSOLIDADO (ampliación del manual)


PERMISOS_POR_ROL: dict[Rol, frozenset[Permiso]] = {
    Rol.CONTADOR: frozenset(
        {
            Permiso.LEER,
            Permiso.RESOLVER_TRANSACCIONES,
            Permiso.ADMINISTRAR_CATALOGO,
            Permiso.CONSOLIDAR_REPORTES,
            Permiso.EXPORTAR_REPORTES,
            Permiso.ANULAR_REPORTES,
        }
    ),
    Rol.AUXILIAR: frozenset({Permiso.LEER, Permiso.CARGAR_FACTURAS, Permiso.CONSOLIDAR_REPORTES}),
    # El ADMIN solo administra usuarios: no lee ni resuelve transacciones ni edita reglas.
    Rol.ADMIN: frozenset({Permiso.ADMINISTRAR_USUARIOS}),
}

PERMISO_POR_RUTA: dict[tuple[str, str], Permiso] = {
    # Facturas
    ("POST", "/facturas/clasificar"): Permiso.CARGAR_FACTURAS,
    ("POST", "/carpeta/procesar"): Permiso.CARGAR_FACTURAS,
    # Transacciones
    ("GET", "/transacciones"): Permiso.LEER,
    ("GET", "/transacciones/{id_transaccion}"): Permiso.LEER,
    ("GET", "/transacciones/{id_transaccion}/eventos"): Permiso.LEER,
    ("GET", "/transacciones/{id_transaccion}/documento"): Permiso.LEER,
    ("POST", "/transacciones/{id_transaccion}/aprobar"): Permiso.RESOLVER_TRANSACCIONES,
    ("POST", "/transacciones/{id_transaccion}/corregir"): Permiso.RESOLVER_TRANSACCIONES,
    ("POST", "/transacciones/{id_transaccion}/rechazar"): Permiso.RESOLVER_TRANSACCIONES,
    # Catálogo y reglas (RF09)
    ("GET", "/catalogo/cuentas"): Permiso.LEER,
    ("POST", "/catalogo/cuentas"): Permiso.ADMINISTRAR_CATALOGO,
    ("GET", "/reglas"): Permiso.LEER,
    ("GET", "/reglas/{codigo}"): Permiso.LEER,
    ("GET", "/reglas/{codigo}/historial"): Permiso.LEER,
    ("POST", "/reglas"): Permiso.ADMINISTRAR_CATALOGO,
    ("PUT", "/reglas/{codigo}"): Permiso.ADMINISTRAR_CATALOGO,
    ("POST", "/reglas/{codigo}/activar"): Permiso.ADMINISTRAR_CATALOGO,
    ("POST", "/reglas/{codigo}/desactivar"): Permiso.ADMINISTRAR_CATALOGO,
    # Reportes diarios (RF06, RF07). La descarga del XML es lectura: el auxiliar lo carga en el ERP.
    ("POST", "/reportes/consolidar"): Permiso.CONSOLIDAR_REPORTES,
    ("GET", "/reportes"): Permiso.LEER,
    ("GET", "/reportes/{id_reporte}"): Permiso.LEER,
    ("GET", "/reportes/{id_reporte}/eventos"): Permiso.LEER,
    ("GET", "/reportes/{id_reporte}/xml"): Permiso.LEER,
    ("POST", "/reportes/{id_reporte}/exportar"): Permiso.EXPORTAR_REPORTES,
    ("POST", "/reportes/{id_reporte}/anular"): Permiso.ANULAR_REPORTES,
    # Usuarios
    ("GET", "/usuarios"): Permiso.ADMINISTRAR_USUARIOS,
    ("POST", "/usuarios"): Permiso.ADMINISTRAR_USUARIOS,
    ("PUT", "/usuarios/{id_usuario}"): Permiso.ADMINISTRAR_USUARIOS,
}

# Rutas sin token.
RUTAS_PUBLICAS: frozenset[tuple[str, str]] = frozenset({("GET", "/salud"), ("POST", "/auth/token")})


def tiene_permiso(rol: Rol, permiso: Permiso) -> bool:
    return permiso in PERMISOS_POR_ROL.get(rol, frozenset())


def autorizar(request: Request, usuario: Annotated[Usuario, Depends(usuario_actual)]) -> Usuario:
    """Sin token → 401 (en usuario_actual). Rol sin el permiso de la ruta → 403."""
    metodo = "GET" if request.method == "HEAD" else request.method
    ruta = request.scope["route"].path
    permiso = PERMISO_POR_RUTA.get((metodo, ruta))
    if permiso is None or not tiene_permiso(usuario.rol, permiso):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"El rol {usuario.rol} no tiene permiso para {metodo} {ruta}."
        )
    return usuario


UsuarioAutorizado = Annotated[Usuario, Depends(autorizar)]
