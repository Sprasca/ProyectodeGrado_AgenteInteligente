"""Crea un usuario del agente. La contraseña se pide sin mostrarla en pantalla.

Uso:
    uv run python scripts/crear_usuario.py <nombre_usuario> --nombre-completo "Nombre Apellido" --rol ADMIN

Roles: CONTADOR, AUXILIAR, ADMIN. El primer usuario debería ser un ADMIN, que luego crea
los demás desde POST /usuarios.
"""

import argparse
import getpass
import sys
from collections.abc import Callable

from agente.domain.enums import Rol
from agente.domain.errors import UsuarioDuplicadoError, UsuarioInvalidoError
from agente.domain.usuarios import Usuario
from agente.infrastructure.db.session import crear_sessionmaker
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError
from agente.infrastructure.db.usuarios import RepositorioUsuarios, RepositorioUsuariosSQL


def main(
    argv: list[str] | None = None,
    leer_contrasena: Callable[[str], str] = getpass.getpass,
    repositorio: RepositorioUsuarios | None = None,
) -> Usuario:
    parser = argparse.ArgumentParser(description="Crea un usuario del agente.")
    parser.add_argument("nombre_usuario", help="3 a 60 caracteres: minúsculas, dígitos, '.', '_' o '-'")
    parser.add_argument("--nombre-completo", required=True)
    parser.add_argument("--rol", required=True, choices=[r.value for r in Rol], type=str.upper)
    args = parser.parse_args(argv)

    contrasena = leer_contrasena("Contraseña: ")
    if leer_contrasena("Repita la contraseña: ") != contrasena:
        sys.exit("Las contraseñas no coinciden. No se creó el usuario.")

    repo = repositorio or RepositorioUsuariosSQL(crear_sessionmaker(connect_timeout=5))
    try:
        usuario = repo.crear(args.nombre_usuario, args.nombre_completo, contrasena, Rol(args.rol))
    except UsuarioInvalidoError as exc:
        sys.exit("No se creó el usuario:\n- " + "\n- ".join(exc.errores))
    except (UsuarioDuplicadoError, RepositorioNoDisponibleError) as exc:
        sys.exit(f"No se creó el usuario: {exc}")
    print(f"Usuario creado: {usuario.nombre_usuario} (id {usuario.id}, rol {usuario.rol}).")
    return usuario


if __name__ == "__main__":
    main()
