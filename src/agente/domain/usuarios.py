"""Usuarios del agente y sus roles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .enums import Rol
from .errors import UltimoAdministradorError

# Coinciden con usuario.nombre_usuario (String(60)) y usuario.nombre_completo (String(200)).
LONGITUD_MAXIMA_NOMBRE_USUARIO = 60
LONGITUD_MAXIMA_NOMBRE_COMPLETO = 200
LONGITUD_MINIMA_CONTRASENA = 8
_RE_NOMBRE_USUARIO = re.compile(r"[a-z0-9][a-z0-9._-]{2,59}")


@dataclass(frozen=True)
class Usuario:
    """Usuario sin su contraseña (el hash nunca sale del repositorio)."""

    id: int
    nombre_usuario: str
    nombre_completo: str
    rol: Rol
    activo: bool = True
    creado_en: datetime | None = None


def normalizar_nombre_usuario(nombre_usuario: str) -> str:
    return nombre_usuario.strip().lower()


def validar_usuario_nuevo(nombre_usuario: str, nombre_completo: str, contrasena: str) -> tuple[str, ...]:
    """Errores de un usuario nuevo (el nombre repetido lo detecta el repositorio)."""
    errores: list[str] = []
    nombre = normalizar_nombre_usuario(nombre_usuario)
    if not _RE_NOMBRE_USUARIO.fullmatch(nombre):
        errores.append(
            "El nombre de usuario debe tener entre 3 y 60 caracteres: letras minúsculas, dígitos, '.', '_' o '-', "
            "y empezar por letra o dígito."
        )
    errores.extend(validar_nombre_completo(nombre_completo))
    if len(contrasena) < LONGITUD_MINIMA_CONTRASENA:
        errores.append(f"La contraseña debe tener al menos {LONGITUD_MINIMA_CONTRASENA} caracteres.")
    return tuple(errores)


def validar_nombre_completo(nombre_completo: str) -> tuple[str, ...]:
    nombre = nombre_completo.strip()
    if not nombre:
        return ("El nombre completo no puede estar vacío.",)
    if len(nombre) > LONGITUD_MAXIMA_NOMBRE_COMPLETO:
        return (f"El nombre completo no puede tener más de {LONGITUD_MAXIMA_NOMBRE_COMPLETO} caracteres.",)
    return ()


def validar_cambio_de_administrador(
    usuario: Usuario, rol: Rol, activo: bool, otros_admins_activos: int
) -> None:
    """Impide dejar el sistema sin ningún ADMIN activo (nadie podría volver a administrar usuarios)."""
    deja_de_ser_admin_activo = usuario.rol is Rol.ADMIN and usuario.activo and (rol is not Rol.ADMIN or not activo)
    if deja_de_ser_admin_activo and otros_admins_activos == 0:
        raise UltimoAdministradorError(
            f"{usuario.nombre_usuario} es el único ADMIN activo: cree o active otro ADMIN antes de "
            "quitarle el rol o desactivarlo."
        )
