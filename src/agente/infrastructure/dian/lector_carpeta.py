"""Lee facturas .xml y .pdf desde una carpeta origen configurable."""

from dataclasses import dataclass
from pathlib import Path

from ...config import get_settings
from ...domain.errors import RutaNoPermitidaError
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


def carpeta_permitida(ruta: str | None, entrada: Path, predeterminada: Path) -> Path:
    """
    Resuelve la carpeta pedida (relativa a DIRECTORIO_ENTRADA o absoluta) siguiendo "..",
    y enlaces simbólicos, y exige que quede DENTRO de DIRECTORIO_ENTRADA.
    Sin ruta se usa la predeterminada (DIRECTORIO_FACTURAS), que también debe quedar dentro.
    """
    raiz = entrada.resolve()
    destino = (raiz / ruta.replace("\\", "/")).resolve() if ruta else predeterminada.resolve()
    if not destino.is_relative_to(raiz):
        raise RutaNoPermitidaError(f"La carpeta {ruta or destino} está fuera del directorio de entrada permitido.")
    return destino


def leer_carpeta(
    carpeta: str | Path | None = None, recursivo: bool = False, raiz: Path | None = None
) -> list[ArchivoLeido]:
    """
    Lee todos los .xml/.pdf de la carpeta. Un archivo defectuoso no detiene a los demás.
    Con raiz, omite los archivos que (por un enlace simbólico) apunten fuera de ella.
    """
    base = Path(carpeta) if carpeta else get_settings().directorio_facturas
    if not base.is_dir():
        raise FileNotFoundError(f"La carpeta no existe: {base}")
    patron = base.rglob("*") if recursivo else base.glob("*")
    archivos = sorted(p for p in patron if p.is_file() and p.suffix.lower() in EXTENSIONES)
    if raiz is not None:
        limite = raiz.resolve()
        archivos = [p for p in archivos if p.resolve().is_relative_to(limite)]
    resultados: list[ArchivoLeido] = []
    for ruta in archivos:
        try:
            resultados.append(ArchivoLeido(ruta, leer_factura(ruta)))
        except Exception as exc:
            resultados.append(ArchivoLeido(ruta, None, str(exc)))
    return resultados