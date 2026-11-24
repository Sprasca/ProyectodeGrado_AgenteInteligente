"""Recepción automática de facturas desde DIRECTORIO_ENTRADA (RF01).

Por cada archivo XML o PDF que aparece en el directorio de entrada:
1. espera a que su escritura haya concluido (tamaño y fecha estables) y lo lee;
2. valida que sea un XML o PDF completo y legible (validación mínima del anexo técnico);
3. lo procesa con el MISMO flujo de la carga por la API (clasificación híbrida y persistencia,
   con canal "carpeta" y sin usuario);
4. lo mueve a recibidos/ o, si falla, a no_recibidos/ junto con un <archivo>.motivo.json, y deja
   un registro de nivel ERROR (la "alerta técnica" del manual).
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..config import Settings
from ..domain.documento import DocumentoOriginal
from ..domain.enums import CanalRecepcion, TipoDocumento
from ..infrastructure.db.transacciones import RepositorioNoDisponibleError
from ..infrastructure.dian.documentos import tipo_por_nombre
from ..infrastructure.dian.validacion import validar_documento

log = logging.getLogger("agente.recepcion")

SUFIJO_MOTIVO = ".motivo.json"

# procesar(contenido, nombre_archivo, tipo) -> id de la transacción registrada
Procesador = Callable[[bytes, str, TipoDocumento], uuid.UUID]


class ArchivoEnEscrituraError(Exception):
    """El archivo todavía se está copiando, está vacío o lo bloquea otro proceso."""


# Únicas causas que se reintentan: el archivo aún no se puede leer, o la base de datos no responde.
ERRORES_TEMPORALES = (ArchivoEnEscrituraError, RepositorioNoDisponibleError)


@dataclass(frozen=True)
class ConfiguracionRecepcion:
    entrada: Path
    recibidos: Path
    no_recibidos: Path
    tamano_maximo: int
    # Segundos que el tamaño y la fecha del archivo deben mantenerse para dar la escritura por concluida.
    espera_estabilidad: float = 1.0
    # Reintentos ante una causa temporal (RF01 1.a): archivo bloqueado o incompleto, base de datos sin respuesta.
    reintentos: int = 3
    # Espera antes del primer reintento; se duplica en cada uno (espera incremental): 1, 2, 4... segundos.
    espera_inicial: float = 1.0

    @classmethod
    def desde_settings(cls, settings: Settings) -> ConfiguracionRecepcion:
        return cls(
            entrada=settings.directorio_entrada,
            recibidos=settings.directorio_recibidos,
            no_recibidos=settings.directorio_no_recibidos,
            tamano_maximo=settings.tamano_maximo_documento,
            espera_estabilidad=settings.recepcion_espera_estabilidad,
            reintentos=settings.recepcion_reintentos,
            espera_inicial=settings.recepcion_espera_inicial,
        )

    def espera_antes_del_reintento(self, reintento: int) -> float:
        """reintento = 1, 2, 3... → espera_inicial, el doble, el cuádruple..."""
        return self.espera_inicial * 2 ** (reintento - 1)


@dataclass(frozen=True)
class Recepcion:
    """Resultado de recibir un archivo."""

    archivo: str
    recibido: bool
    destino: Path | None
    id_transaccion: uuid.UUID | None = None
    motivo: str | None = None
    intentos: int = 1


class ReceptorFacturas:
    def __init__(
        self,
        configuracion: ConfiguracionRecepcion,
        procesar: Procesador,
        dormir: Callable[[float], None] = time.sleep,
        ahora: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.configuracion = configuracion
        self._procesar = procesar
        self._dormir = dormir
        self._ahora = ahora

    # --- qué archivos se reciben ---------------------------------------------------------------

    def es_candidato(self, ruta: Path) -> bool:
        """Un .xml o .pdf directamente dentro del directorio de entrada (no en recibidos/ ni no_recibidos/)."""
        try:
            en_entrada = ruta.resolve().parent == self.configuracion.entrada.resolve()
        except OSError:
            return False
        return en_entrada and tipo_por_nombre(ruta.name) is not None and ruta.is_file()

    def pendientes(self) -> list[Path]:
        """
        Archivos que ya están en la entrada (al arrancar o al reanudar tras una interrupción, RF01 4.a),
        en orden de llegada: del más antiguo al más reciente.
        """
        try:
            candidatos = [p for p in self.configuracion.entrada.iterdir() if self.es_candidato(p)]
            return sorted(candidatos, key=lambda p: (p.stat().st_mtime_ns, p.name))
        except OSError:
            return []

    # --- recepción de un archivo ------------------------------------------------------------------

    def recibir(self, ruta: Path) -> Recepcion:
        """
        Recibe un archivo. Ante una causa temporal reintenta con espera incremental; agotados los
        reintentos, o ante cualquier otra falla, lo mueve a no_recibidos/ con su motivo.
        """
        intento = 1
        while True:
            try:
                id_transaccion = self._intentar(ruta)
            except FileNotFoundError:
                log.warning("El archivo %s desapareció del directorio de entrada antes de procesarse.", ruta.name)
                return Recepcion(ruta.name, False, None, motivo="El archivo desapareció antes de procesarse.")
            except ERRORES_TEMPORALES as exc:
                if intento > self.configuracion.reintentos:
                    agotados = type(exc)(f"{exc} Se agotaron los {self.configuracion.reintentos} reintentos.")
                    return self._no_recibido(ruta, agotados, intento)
                espera = self.configuracion.espera_antes_del_reintento(intento)
                log.warning(
                    "No se pudo recibir %s (intento %d de %d): %s Se reintenta en %g s.",
                    ruta.name,
                    intento,
                    self.configuracion.reintentos + 1,
                    exc,
                    espera,
                )
                self._dormir(espera)
                intento += 1
                continue
            except Exception as exc:  # cualquier otro fallo es definitivo: el archivo va a no_recibidos/
                return self._no_recibido(ruta, exc, intento)
            destino = self._mover(ruta, self.configuracion.recibidos)
            log.info(
                "Factura recibida: %s → transacción %s (%s).",
                ruta.name,
                id_transaccion,
                self._ahora().isoformat(timespec="seconds"),
            )
            return Recepcion(ruta.name, True, destino, id_transaccion, intentos=intento)

    def _intentar(self, ruta: Path) -> uuid.UUID:
        tipo = tipo_por_nombre(ruta.name)
        if tipo is None:
            raise ValueError(f"Solo se reciben archivos .xml o .pdf: {ruta.name}")
        contenido = self._leer_cuando_termine_la_escritura(ruta)
        validar_documento(contenido, tipo, self.configuracion.tamano_maximo)
        return self._procesar(contenido, ruta.name, tipo)

    def _leer_cuando_termine_la_escritura(self, ruta: Path) -> bytes:
        """RF01 paso 3: no se lee hasta que el archivo deja de cambiar."""
        antes = ruta.stat()
        if antes.st_size > self.configuracion.tamano_maximo:
            with ruta.open("rb") as archivo:  # no se carga entero en memoria: la validación lo rechaza
                return archivo.read(self.configuracion.tamano_maximo + 1)
        self._dormir(self.configuracion.espera_estabilidad)
        despues = ruta.stat()
        if (antes.st_size, antes.st_mtime_ns) != (despues.st_size, despues.st_mtime_ns):
            raise ArchivoEnEscrituraError("El archivo todavía se está escribiendo.")
        if despues.st_size == 0:
            raise ArchivoEnEscrituraError("El archivo está vacío (puede que aún se esté copiando).")
        try:
            with ruta.open("rb") as archivo:
                return archivo.read()
        except PermissionError as exc:
            raise ArchivoEnEscrituraError(f"El archivo está bloqueado por otro proceso: {exc}") from exc

    # --- movimiento y registro del motivo ---------------------------------------------------------------

    def _no_recibido(self, ruta: Path, exc: Exception, intentos: int = 1) -> Recepcion:
        motivo = str(exc) or type(exc).__name__
        destino = self._mover(ruta, self.configuracion.no_recibidos)
        registro = {
            "archivo": ruta.name,
            "movido_a": str(destino) if destino else None,
            "fecha": self._ahora().isoformat(timespec="seconds"),
            "motivo": motivo,
            "tipo_error": type(exc).__name__,
            "intentos": intentos,
        }
        if destino is not None:
            destino.with_name(destino.name + SUFIJO_MOTIVO).write_text(
                json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        # Alerta técnica (RF01 1.a y 2.a): registro de nivel ERROR con el motivo.
        log.error("Factura NO recibida: %s → %s. Motivo: %s", ruta.name, destino or "(no se pudo mover)", motivo)
        return Recepcion(ruta.name, False, destino, motivo=motivo, intentos=intentos)

    def _mover(self, ruta: Path, carpeta: Path) -> Path | None:
        """Mueve el archivo sin sobrescribir: si ya existe uno con ese nombre, agrega fecha y hora."""
        try:
            carpeta.mkdir(parents=True, exist_ok=True)
            destino = carpeta / ruta.name
            if destino.exists():
                marca = self._ahora().strftime("%Y%m%dT%H%M%S%fZ")
                destino = carpeta / f"{ruta.stem}__{marca}{ruta.suffix}"
                contador = 1
                while destino.exists():
                    destino = carpeta / f"{ruta.stem}__{marca}_{contador}{ruta.suffix}"
                    contador += 1
            return Path(shutil.move(str(ruta), str(destino)))
        except OSError as exc:
            log.error("No se pudo mover %s a %s: %s", ruta.name, carpeta, exc)
            return None


def procesador_por_defecto(usar_nlp: bool = True) -> Procesador:
    """El mismo flujo de la carga por la API: catálogo según FUENTE_CATALOGO, clasificación y persistencia."""
    from ..infrastructure.db.session import fabrica_compartida
    from ..infrastructure.db.transacciones import RepositorioSQL
    from .clasificar_con_nlp import catalogo_por_defecto
    from .registrar_documento import clasificar_documento, guardar_documento

    def procesar(contenido: bytes, nombre: str, tipo: TipoDocumento) -> uuid.UUID:
        catalogo = catalogo_por_defecto()
        resultado = clasificar_documento(contenido, tipo, catalogo, usar_nlp=usar_nlp)
        documento = DocumentoOriginal.recibido(nombre, tipo, contenido)
        repositorio = RepositorioSQL(fabrica_compartida(), catalogo)
        # Sin usuario: la recepción automática no la hace nadie (igual que la clasificación automática).
        return guardar_documento(resultado, documento, repositorio, canal=CanalRecepcion.CARPETA, usuario_id=None)

    return procesar
