"""Usuarios del agente en PostgreSQL. El hash de la contraseña nunca sale de este módulo."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import Rol
from ...domain.errors import UsuarioDuplicadoError, UsuarioInvalidoError, UsuarioNoEncontradoError
from ...domain.usuarios import (
    Usuario,
    normalizar_nombre_usuario,
    validar_cambio_de_administrador,
    validar_nombre_completo,
    validar_usuario_nuevo,
)
from ..seguridad.contrasenas import cifrar, hash_ficticio, requiere_recifrar, verificar
from .models import UsuarioModel
from .transacciones import RepositorioNoDisponibleError

_VIOLACION_UNICA = "23505"


class RepositorioUsuarios(Protocol):
    def crear(self, nombre_usuario: str, nombre_completo: str, contrasena: str, rol: Rol) -> Usuario:
        """UsuarioInvalidoError con datos inválidos; UsuarioDuplicadoError si el nombre ya existe."""
        ...

    def obtener(self, id_usuario: int) -> Usuario | None: ...

    def listar(self) -> list[Usuario]: ...

    def actualizar(
        self,
        id_usuario: int,
        *,
        rol: Rol | None = None,
        activo: bool | None = None,
        nombre_completo: str | None = None,
    ) -> Usuario:
        """Cambia rol, estado o nombre. UltimoAdministradorError si dejaría el sistema sin ADMIN activo."""
        ...

    def autenticar(self, nombre_usuario: str, contrasena: str) -> Usuario | None:
        """El usuario si existe, está activo y la contraseña coincide; si no, None."""
        ...


def _a_usuario(fila: UsuarioModel) -> Usuario:
    return Usuario(
        id=fila.id,
        nombre_usuario=fila.nombre_usuario,
        nombre_completo=fila.nombre_completo,
        rol=Rol(fila.rol),
        activo=fila.activo,
        creado_en=fila.creado_en,
    )


class RepositorioUsuariosSQL:
    def __init__(self, fabrica_sesiones: sessionmaker[Session]) -> None:
        self._fabrica = fabrica_sesiones

    @contextmanager
    def _transaccion(self) -> Iterator[Session]:
        try:
            with self._fabrica() as sesion, sesion.begin():
                yield sesion
        except (OperationalError, InterfaceError) as exc:
            raise RepositorioNoDisponibleError(
                "No hay conexión con PostgreSQL. Verifique que la base esté levantada "
                "(docker compose up -d db) y que DATABASE_URL sea correcta."
            ) from exc

    def crear(self, nombre_usuario: str, nombre_completo: str, contrasena: str, rol: Rol) -> Usuario:
        if errores := validar_usuario_nuevo(nombre_usuario, nombre_completo, contrasena):
            raise UsuarioInvalidoError(errores)
        nombre = normalizar_nombre_usuario(nombre_usuario)
        duplicado = UsuarioDuplicadoError(f"Ya existe el usuario {nombre}.")
        try:
            with self._transaccion() as sesion:
                if sesion.scalar(select(UsuarioModel.id).where(UsuarioModel.nombre_usuario == nombre)) is not None:
                    raise duplicado
                fila = UsuarioModel(
                    nombre_usuario=nombre,
                    nombre_completo=nombre_completo.strip(),
                    hash_contrasena=cifrar(contrasena),
                    rol=Rol(rol).value,
                    activo=True,
                )
                sesion.add(fila)
                sesion.flush()
                sesion.refresh(fila)
                return _a_usuario(fila)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == _VIOLACION_UNICA:
                raise duplicado from exc
            raise

    def obtener(self, id_usuario: int) -> Usuario | None:
        with self._transaccion() as sesion:
            fila = sesion.get(UsuarioModel, id_usuario)
            return _a_usuario(fila) if fila is not None else None

    def listar(self) -> list[Usuario]:
        with self._transaccion() as sesion:
            return [_a_usuario(f) for f in sesion.scalars(select(UsuarioModel).order_by(UsuarioModel.id))]

    def actualizar(
        self,
        id_usuario: int,
        *,
        rol: Rol | None = None,
        activo: bool | None = None,
        nombre_completo: str | None = None,
    ) -> Usuario:
        if nombre_completo is not None and (errores := validar_nombre_completo(nombre_completo)):
            raise UsuarioInvalidoError(errores)
        with self._transaccion() as sesion:
            fila = sesion.get(UsuarioModel, id_usuario, with_for_update=True)
            if fila is None:
                raise UsuarioNoEncontradoError(f"No existe el usuario {id_usuario}.")
            actual = _a_usuario(fila)
            nuevo_rol = Rol(rol) if rol is not None else actual.rol
            nuevo_activo = activo if activo is not None else actual.activo
            # Bloquea las filas de ADMIN activos: dos cambios simultáneos no pueden dejar el sistema sin ADMIN.
            otros_admins = len(
                sesion.scalars(
                    select(UsuarioModel.id)
                    .where(UsuarioModel.rol == Rol.ADMIN.value, UsuarioModel.activo, UsuarioModel.id != id_usuario)
                    .with_for_update()
                ).all()
            )
            validar_cambio_de_administrador(actual, nuevo_rol, nuevo_activo, otros_admins)
            fila.rol = nuevo_rol.value
            fila.activo = nuevo_activo
            if nombre_completo is not None:
                fila.nombre_completo = nombre_completo.strip()
            sesion.flush()
            return _a_usuario(fila)

    def autenticar(self, nombre_usuario: str, contrasena: str) -> Usuario | None:
        nombre = normalizar_nombre_usuario(nombre_usuario)
        with self._transaccion() as sesion:
            fila = sesion.scalar(select(UsuarioModel).where(UsuarioModel.nombre_usuario == nombre))
            if fila is None:
                verificar(hash_ficticio(), contrasena)  # mismo tiempo de respuesta exista o no el usuario
                return None
            if not verificar(fila.hash_contrasena, contrasena) or not fila.activo:
                return None
            if requiere_recifrar(fila.hash_contrasena):
                fila.hash_contrasena = cifrar(contrasena)
            return _a_usuario(fila)
