"""Lee facturas .xml y .pdf desde una carpeta origen configurable."""

from dataclasses import dataclass
from pathlib import Path

from ...config import get_settings
from ...domain.factura import Factura
from .lector_pdf import factura_desde_pdf
from .parser import parsear_xml

EXTENSIONES = {".xml", ".pdf"}


@dataclass(frozen=True)
class ArchivoLeido:
    ruta: Path
    factura: Factura | None
    error: str | None = None


def leer_factura(ruta: Path) -> Factura:
    contenido = ruta.read_bytes()
    if ruta.suffix.lower() == ".pdf":
        return factura_desde_pdf(contenido)
    return parsear_xml(contenido)


def leer_carpeta(carpeta: str | Path | None = None, recursivo: bool = False) -> list[ArchivoLeido]:
    """Lee todos los .xml/.pdf de la carpeta. Un archivo defectuoso no detiene a los demás."""
    base = Path(carpeta) if carpeta else get_settings().directorio_facturas
    if not base.is_dir():
        raise FileNotFoundError(f"La carpeta no existe: {base}")
    patron = base.rglob("*") if recursivo else base.glob("*")
    archivos = sorted(p for p in patron if p.is_file() and p.suffix.lower() in EXTENSIONES)
    resultados: list[ArchivoLeido] = []
    for ruta in archivos:
        try:
            resultados.append(ArchivoLeido(ruta, leer_factura(ruta)))
        except Exception as exc:
            resultados.append(ArchivoLeido(ruta, None, str(exc)))
    return resultados