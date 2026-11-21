"""Autenticación: OAuth2 con contraseña y tokens JWT firmados con JWT_SECRETO (HS256)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from ..config import Settings, get_settings
from ..domain.usuarios import Usuario
from ..infrastructure.db.session import fabrica_compartida
from ..infrastructure.db.usuarios import RepositorioUsuarios, RepositorioUsuariosSQL

ALGORITMO = "HS256"
# HS256 necesita al menos 256 bits de clave: con menos, el token se puede forzar por fuerza bruta.
LONGITUD_MINIMA_SECRETO = 32


class ConfiguracionInseguraError(RuntimeError):
    """Falta JWT_SECRETO o es demasiado corto: la API no debe arrancar."""


def validar_secreto(settings: Settings) -> None:
    if not settings.jwt_secreto:
        raise ConfiguracionInseguraError(
            "Falta JWT_SECRETO: defínalo en .env (por ejemplo con "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\"). La API no arranca sin él."
        )
    if len(settings.jwt_secreto) < LONGITUD_MINIMA_SECRETO:
        raise ConfiguracionInseguraError(
            f"JWT_SECRETO debe tener al menos {LONGITUD_MINIMA_SECRETO} caracteres; "
            "la API no arranca con uno más corto."
        )


def crear_token(usuario: Usuario, settings: Settings | None = None, ahora: datetime | None = None) -> str:
    settings = settings or get_settings()
    emitido = ahora or datetime.now(UTC)
    datos = {
        "sub": str(usuario.id),
        "usuario": usuario.nombre_usuario,  # informativo: los permisos se leen de la base en cada petición
        "iat": emitido,
        "exp": emitido + timedelta(minutes=settings.jwt_minutos),
    }
    return jwt.encode(datos, settings.jwt_secreto, algorithm=ALGORITMO)


def _no_autenticado(detalle: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detalle, headers={"WWW-Authenticate": "Bearer"})


def id_usuario_del_token(token: str, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    try:
        datos = jwt.decode(
            token, settings.jwt_secreto, algorithms=[ALGORITMO], options={"require": ["exp", "iat", "sub"]}
        )
        return int(datos["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise _no_autenticado("El token expiró: solicite uno nuevo en POST /auth/token.") from exc
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise _no_autenticado("Token inválido.") from exc


_esquema = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def get_repositorio_usuarios() -> RepositorioUsuarios:
    """Usuarios en PostgreSQL. Las pruebas lo reemplazan con app.dependency_overrides."""
    return RepositorioUsuariosSQL(fabrica_compartida())


RepoUsuarios = Annotated[RepositorioUsuarios, Depends(get_repositorio_usuarios)]


def usuario_actual(token: Annotated[str | None, Depends(_esquema)], usuarios: RepoUsuarios) -> Usuario:
    """Usuario del token. 401 si falta el token, es inválido o expiró, o el usuario no existe o está inactivo."""
    if not token:
        raise _no_autenticado("Falta el token: inicie sesión en POST /auth/token y envíe Authorization: Bearer.")
    usuario = usuarios.obtener(id_usuario_del_token(token))
    if usuario is None or not usuario.activo:
        raise _no_autenticado("El usuario del token no existe o está inactivo.")
    return usuario


UsuarioActual = Annotated[Usuario, Depends(usuario_actual)]
