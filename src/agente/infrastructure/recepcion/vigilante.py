"""Vigilante de DIRECTORIO_ENTRADA con watchdog (RF01 pasos 1 y 2, casos 3.a y 4.a).

- watchdog avisa cuando aparece, cambia o llega (por renombre) un archivo en la entrada; el aviso
  solo lo pone en cola. Un único hilo trabajador procesa la cola en orden, con ReceptorFacturas.
- Cada `intervalo` segundos una revisión:
  * comprueba que el directorio exista y sea accesible; si no, deja un ERROR (alerta técnica, 3.a),
    detiene la observación y la reanuda cuando el directorio vuelve;
  * vuelve a mirar los archivos presentes, para procesar los que llegaron durante una interrupción
    (4.a) o cuyo aviso se perdió: ningún documento queda sin ingresar.
- Solo se observa el primer nivel de la entrada: recibidos/ y no_recibidos/ no se reprocesan.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ...application.recepcion import ReceptorFacturas

log = logging.getLogger("agente.recepcion")

_FIN = object()  # marca para detener al trabajador


class _Avisos(FileSystemEventHandler):
    def __init__(self, encolar: Callable[[Path], None]) -> None:
        self._encolar = encolar

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._encolar(Path(str(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._encolar(Path(str(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        # Muchos sistemas escriben un temporal y lo renombran al final: cuenta el destino.
        if not event.is_directory:
            self._encolar(Path(str(event.dest_path)))


class VigilanteEntrada:
    def __init__(
        self,
        receptor: ReceptorFacturas,
        intervalo: float = 5.0,
        crear_observador: Callable[[], Any] = Observer,
    ) -> None:
        self.receptor = receptor
        self.intervalo = intervalo
        self._crear_observador = crear_observador
        self._observador: Any | None = None
        self._disponible: bool | None = None  # None: todavía no se ha revisado
        self._cola: queue.Queue[Any] = queue.Queue()
        self._en_cola: set[Path] = set()
        self._candado = threading.Lock()
        # Archivos que no se pudieron mover (p. ej. bloqueados): no se reprocesan mientras no cambien.
        self._sin_mover: dict[Path, int] = {}
        self._trabajador: threading.Thread | None = None

    @property
    def entrada(self) -> Path:
        return self.receptor.configuracion.entrada

    # --- cola -----------------------------------------------------------------------------------

    def encolar(self, ruta: Path) -> bool:
        """Pone el archivo en cola si es un candidato y no está ya en ella. Devuelve si lo encoló."""
        if not self.receptor.es_candidato(ruta):
            return False
        clave = ruta.resolve()
        with self._candado:
            if clave in self._en_cola:
                return False
            marca = self._sin_mover.get(clave)
            if marca is not None:
                try:
                    if ruta.stat().st_mtime_ns == marca:
                        return False
                except OSError:
                    return False
                del self._sin_mover[clave]
            self._en_cola.add(clave)
        self._cola.put(clave)
        return True

    def atender_cola(self, bloquear: bool = False) -> int:
        """Procesa lo que haya en la cola (para el trabajador y para las pruebas). Devuelve cuántos procesó."""
        procesados = 0
        while True:
            try:
                ruta = self._cola.get(block=bloquear)
            except queue.Empty:
                return procesados
            if ruta is _FIN:
                return procesados
            try:
                recepcion = self.receptor.recibir(ruta)
                if recepcion.destino is None and ruta.exists():
                    with self._candado:
                        self._sin_mover[ruta] = ruta.stat().st_mtime_ns
            except Exception:  # el trabajador nunca debe morir por un archivo
                log.exception("Error inesperado al recibir %s.", ruta.name)
            finally:
                with self._candado:
                    self._en_cola.discard(ruta)
            procesados += 1

    # --- revisión periódica (3.a y 4.a) -------------------------------------------------------------

    def revisar(self) -> None:
        if self._directorio_accesible():
            if not self._disponible:
                if self._disponible is False:
                    log.info("El directorio de entrada %s volvió a estar disponible.", self.entrada)
                self._iniciar_observador()
                log.info("Observando el directorio de entrada %s.", self.entrada)
            self._disponible = True
            for ruta in self.receptor.pendientes():
                self.encolar(ruta)
        else:
            if self._disponible is not False:
                # Alerta técnica (RF01 3.a).
                log.error(
                    "El directorio de entrada %s no está disponible o no es accesible. "
                    "Se reanudará la observación cuando vuelva.",
                    self.entrada,
                )
                self._detener_observador()
            self._disponible = False

    def _directorio_accesible(self) -> bool:
        try:
            with os.scandir(self.entrada):  # abre el directorio de verdad: existe y hay permiso de lectura
                return True
        except OSError:
            return False

    def _iniciar_observador(self) -> None:
        self._detener_observador()
        observador = self._crear_observador()
        observador.schedule(_Avisos(self.encolar), str(self.entrada), recursive=False)
        try:
            observador.start()
        except OSError as exc:
            log.error("No se pudo observar %s: %s", self.entrada, exc)
            self._disponible = False
            return
        self._observador = observador

    def _detener_observador(self) -> None:
        if self._observador is not None:
            try:
                self._observador.stop()
                self._observador.join(timeout=5)
            except Exception:  # un directorio desaparecido puede romper el observador al detenerlo
                log.debug("El observador anterior no se detuvo limpiamente.", exc_info=True)
            self._observador = None

    # --- ciclo de vida -------------------------------------------------------------------------------

    def ejecutar(self, detener: threading.Event) -> None:
        """Observa hasta que `detener` se active (p. ej. con Ctrl+C en scripts/recibir_facturas.py)."""
        self._trabajador = threading.Thread(target=self.atender_cola, kwargs={"bloquear": True}, daemon=True)
        self._trabajador.start()
        try:
            while not detener.is_set():
                self.revisar()
                detener.wait(self.intervalo)
        finally:
            self._detener_observador()
            self._cola.put(_FIN)
            self._trabajador.join(timeout=30)
            log.info("Recepción automática detenida.")
