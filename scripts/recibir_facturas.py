"""Recepción automática de facturas (RF01): vigila DIRECTORIO_ENTRADA y procesa cada XML o PDF nuevo.

Cada factura pasa por el mismo flujo de la carga por la API (clasificación híbrida y persistencia),
con canal "carpeta" y sin usuario. Los archivos procesados van a DIRECTORIO_RECIBIDOS y los que
fallan a DIRECTORIO_NO_RECIBIDOS, con un <archivo>.motivo.json.

El mismo proceso hace el cierre diario programado (RF06): un solo intento a la HORA_CIERRE de Colombia
(guardada en la base: GET/PUT /reportes/horario), o al arrancar si esa hora ya pasó y la jornada no tiene
cierre ni un intento registrado. Si no puede cerrar, registra la alerta técnica JORNADA_SIN_CIERRE.

Uso:
    uv run python scripts/recibir_facturas.py               # vigila y hace el cierre diario, hasta Ctrl+C
    uv run python scripts/recibir_facturas.py --una-vez     # procesa lo pendiente y termina (sin cierre)
    uv run python scripts/recibir_facturas.py --sin-nlp     # solo reglas, sin consultar a Groq
    uv run python scripts/recibir_facturas.py --sin-cierre  # vigila sin hacer el cierre diario
"""

import argparse
import logging
import signal
import threading

from agente.application.cierre import cierre_por_defecto
from agente.application.notificaciones import despachador_por_defecto
from agente.application.recepcion import ConfiguracionRecepcion, ReceptorFacturas, receptor_por_defecto
from agente.config import get_settings
from agente.infrastructure.recepcion.vigilante import VigilanteEntrada


def construir_receptor(usar_nlp: bool = True) -> ReceptorFacturas:
    return receptor_por_defecto(ConfiguracionRecepcion.desde_settings(get_settings()), usar_nlp)


def notificaciones_por_defecto():
    from agente.infrastructure.db.notificaciones import RepositorioNotificacionesSQL
    from agente.infrastructure.db.session import fabrica_compartida

    return RepositorioNotificacionesSQL(fabrica_compartida())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recepción automática de facturas desde DIRECTORIO_ENTRADA.")
    parser.add_argument("--una-vez", action="store_true", help="procesa los archivos pendientes y termina")
    parser.add_argument("--sin-nlp", action="store_true", help="clasifica solo con reglas (no consulta a Groq)")
    parser.add_argument("--intervalo", type=float, default=5.0, help="segundos entre revisiones del directorio")
    parser.add_argument("--sin-cierre", action="store_true", help="no hace el cierre diario programado (HORA_CIERRE)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    receptor = construir_receptor(usar_nlp=not args.sin_nlp)

    if args.una_vez:
        vigilante = VigilanteEntrada(receptor, intervalo=args.intervalo)
        vigilante.revisar()
        procesados = vigilante.atender_cola()
        logging.getLogger("agente.recepcion").info("Procesados %d archivos pendientes.", procesados)
        return 0

    tareas = []
    if not args.sin_cierre:
        cierre = cierre_por_defecto()
        logging.getLogger("agente.reportes").info(
            "Cierre diario programado a la HORA_CIERRE guardada en la base (hora de Colombia)."
        )
        tareas.append(cierre.revisar)
    settings = get_settings()
    if settings.correo_activo:
        tareas.append(despachador_por_defecto(settings).revisar)
        logging.getLogger("agente.notificaciones").info(
            "Notificaciones por correo activas (SMTP %s:%s).", settings.smtp_host, settings.smtp_puerto
        )
    vigilante = VigilanteEntrada(
        receptor, intervalo=args.intervalo, tareas=tareas, notificaciones=notificaciones_por_defecto()
    )
    detener = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: detener.set())
    signal.signal(signal.SIGTERM, lambda *_: detener.set())
    vigilante.ejecutar(detener)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
