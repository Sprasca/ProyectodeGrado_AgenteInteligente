"""El archivo original de una factura, guardado tal como llegó (no repudio)."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath

from .enums import TipoDocumento

LONGITUD_MAXIMA_NOMBRE = 255


def nombre_archivo_seguro(nombre: str | None, tipo: TipoDocumento) -> str:
    """Solo el nombre (sin carpetas, sin caracteres de control), de hasta 255 caracteres."""
    base = PureWindowsPath(nombre or "").name  # quita rutas con "/" o "\\"
    base = "".join(c for c in base if unicodedata.category(c)[0] != "C").strip()
    if not base or base in (".", ".."):
        base = f"documento.{tipo.value.lower()}"
    return base[-LONGITUD_MAXIMA_NOMBRE:]


@dataclass(frozen=True)
class DocumentoOriginal:
    nombre_archivo: str
    tipo: TipoDocumento
    contenido: bytes = field(repr=False)
    recibido_en: datetime | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.contenido).hexdigest()

    @classmethod
    def recibido(cls, nombre: str | None, tipo: TipoDocumento, contenido: bytes) -> DocumentoOriginal:
        return cls(nombre_archivo_seguro(nombre, tipo), tipo, contenido)
