"""Repositorio de usuarios en memoria. SOLO para pruebas; mismo comportamiento que RepositorioUsuariosSQL."""

from dataclasses import replace
from datetime import UTC, datetime
from itertools import count

from agente.domain.enums import Rol
from agente.domain.errors import UsuarioDuplicadoError, UsuarioInvalidoError, UsuarioNoEncontradoError
from agente.domain.usuarios import (
    Usuario,
    normalizar_email,
    normalizar_nombre_usuario,
    validar_cambio_de_administrador,
    validar_email,
    validar_nombre_completo,
    validar_usuario_nuevo,
)
from agente.infrastructure.seguridad.contrasenas import cifrar, hash_ficticio, verificar


class RepositorioUsuariosMemoria:
    def __init__(self) -> None:
        self._usuarios: dict[int, Usuario] = {}
        self._hashes: dict[int, str] = {}
        self._ids = count(1)

    def crear(
        self, nombre_usuario: str, nombre_completo: str, contrasena: str, rol: Rol, email: str | None = None
    ) -> Usuario:
        if errores := validar_usuario_nuevo(nombre_usuario, nombre_completo, contrasena) + validar_email(email):
            raise UsuarioInvalidoError(errores)
        nombre = normalizar_nombre_usuario(nombre_usuario)
        if any(u.nombre_usuario == nombre for u in self._usuarios.values()):
            raise UsuarioDuplicadoError(f"Ya existe el usuario {nombre}.")
        usuario = Usuario(
            next(self._ids), nombre, nombre_completo.strip(), Rol(rol), True, datetime.now(UTC), normalizar_email(email)
        )
        self._usuarios[usuario.id] = usuario
        self._hashes[usuario.id] = cifrar(contrasena)
        return usuario

    def obtener(self, id_usuario: int) -> Usuario | None:
        return self._usuarios.get(id_usuario)

    def listar(self) -> list[Usuario]:
        return sorted(self._usuarios.values(), key=lambda u: u.id)

    def actualizar(
        self,
        id_usuario: int,
        *,
        rol: Rol | None = None,
        activo: bool | None = None,
        nombre_completo: str | None = None,
        email: str | None = None,
    ) -> Usuario:
        if nombre_completo is not None and (errores := validar_nombre_completo(nombre_completo)):
            raise UsuarioInvalidoError(errores)
        if errores := validar_email(email):
            raise UsuarioInvalidoError(errores)
        actual = self._usuarios.get(id_usuario)
        if actual is None:
            raise UsuarioNoEncontradoError(f"No existe el usuario {id_usuario}.")
        nuevo_rol = Rol(rol) if rol is not None else actual.rol
        nuevo_activo = activo if activo is not None else actual.activo
        otros_admins = sum(
            1 for u in self._usuarios.values() if u.id != id_usuario and u.rol is Rol.ADMIN and u.activo
        )
        validar_cambio_de_administrador(actual, nuevo_rol, nuevo_activo, otros_admins)
        nuevo = replace(
            actual,
            rol=nuevo_rol,
            activo=nuevo_activo,
            nombre_completo=nombre_completo.strip() if nombre_completo is not None else actual.nombre_completo,
            email=normalizar_email(email) if email is not None else actual.email,
        )
        self._usuarios[id_usuario] = nuevo
        return nuevo

    def autenticar(self, nombre_usuario: str, contrasena: str) -> Usuario | None:
        nombre = normalizar_nombre_usuario(nombre_usuario)
        usuario = next((u for u in self._usuarios.values() if u.nombre_usuario == nombre), None)
        if usuario is None:
            verificar(hash_ficticio(), contrasena)
            return None
        if not verificar(self._hashes[usuario.id], contrasena) or not usuario.activo:
            return None
        return usuario
