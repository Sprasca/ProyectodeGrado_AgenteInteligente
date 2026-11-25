"""Usuarios y tokens de prueba por rol (sin base de datos).

usuarios_de_prueba() crea siempre los mismos tres usuarios, en el mismo orden, para que sus
ids coincidan con los de los tokens que devuelve encabezados(rol).
"""

from repositorio_usuarios_memoria import RepositorioUsuariosMemoria

from agente.api.autenticacion import crear_token
from agente.domain.enums import Rol
from agente.domain.usuarios import Usuario

CONTRASENA = "clave-de-prueba-123"

USUARIOS = {
    Rol.CONTADOR: Usuario(1, "contadora", "Contadora de Prueba", Rol.CONTADOR),
    Rol.AUXILIAR: Usuario(2, "auxiliar", "Auxiliar de Prueba", Rol.AUXILIAR),
    Rol.ADMIN: Usuario(3, "administrador", "Administrador de Prueba", Rol.ADMIN),
}


def usuarios_de_prueba() -> RepositorioUsuariosMemoria:
    repo = RepositorioUsuariosMemoria()
    for rol, usuario in USUARIOS.items():
        creado = repo.crear(usuario.nombre_usuario, usuario.nombre_completo, CONTRASENA, rol)
        assert creado.id == usuario.id
    return repo


def ruta_concreta(ruta: str) -> str:
    """Rellena los parámetros de una ruta de la API con valores de prueba."""
    return (
        ruta.replace("{id_transaccion}", "6f1c0b6e-2f2a-4a6e-9d7c-1a2b3c4d5e6f")
        .replace("{codigo}", "R-4135-COMERCIO")
        .replace("{id_usuario}", "2")
        .replace("{id_reporte}", "0b9a3c1e-4d2f-4c8a-9e7b-5f6a7b8c9d0e")
    )


def encabezados(rol: Rol) -> dict[str, str]:
    """Authorization: Bearer con un token válido del usuario de prueba de ese rol."""
    return {"Authorization": f"Bearer {crear_token(USUARIOS[rol])}"}
