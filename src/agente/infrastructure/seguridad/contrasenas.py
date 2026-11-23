"""Hash de contraseñas con Argon2id (argon2-cffi, parámetros recomendados por la librería)."""

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()


def cifrar(contrasena: str) -> str:
    """Hash Argon2id con sal aleatoria; el resultado incluye los parámetros usados."""
    return _hasher.hash(contrasena)


def verificar(hash_contrasena: str, contrasena: str) -> bool:
    try:
        return _hasher.verify(hash_contrasena, contrasena)
    except (VerificationError, InvalidHashError):
        return False


def requiere_recifrar(hash_contrasena: str) -> bool:
    """True si el hash se hizo con parámetros más débiles que los actuales."""
    return _hasher.check_needs_rehash(hash_contrasena)


@lru_cache
def hash_ficticio() -> str:
    """Para verificar algo aunque el usuario no exista: el tiempo de respuesta no revela si existe."""
    return cifrar("contrasena-ficticia-para-igualar-tiempos")
