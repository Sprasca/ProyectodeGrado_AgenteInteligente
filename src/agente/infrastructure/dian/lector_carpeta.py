"""Lee facturas .xml y .pdf desde una carpeta origen configurable."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ...config import get_settings
from ...domain.enums import TipoDocumento
from ...domain.errors import RutaNoPermitidaError
from ...domain.factura import Factura
from .documentos import leer_documento, tipo_por_nombre

EXTENSIONES = {".xml", ".pdf"}


@dataclass(frozen=True)
class ArchivoLeido:
    ruta: Path
    factura: Factura | None
    error: str | None = None


def leer_factura(ruta: Path) -> Factura:
    return leer_documento(ruta.read_bytes(), tipo_por_nombre(ruta.name) or TipoDocumento.XML)


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
    carpeta: str | Path | None = None,
    recursivo: bool = False,
    raiz: Path | None = None,
    excluir: Iterable[Path] = (),
) -> list[ArchivoLeido]:
    """
    Lee todos los .xml/.pdf de la carpeta. Un archivo defectuoso no detiene a los demás.
    Con raiz, omite los archivos que (por un enlace simbólico) apunten fuera de ella.
    Al recorrer subcarpetas (recursivo), omite las de `excluir` (p. ej. recibidos/ y no_recibidos/ de
    la recepción automática), salvo que sean la carpeta pedida.
    """
    base = Path(carpeta) if carpeta else get_settings().directorio_facturas
    if not base.is_dir():
        raise FileNotFoundError(f"La carpeta no existe: {base}")
    patron = base.rglob("*") if recursivo else base.glob("*")
    archivos = sorted(p for p in patron if p.is_file() and p.suffix.lower() in EXTENSIONES)
    if recursivo:
        origen = base.resolve()
        omitidas = [e.resolve() for e in excluir if e.resolve() != origen]
        archivos = [p for p in archivos if not any(p.resolve().is_relative_to(o) for o in omitidas)]
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