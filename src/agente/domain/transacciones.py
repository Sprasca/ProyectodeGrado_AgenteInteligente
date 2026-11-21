"""Reglas de revisión del contador sobre una transacción ya registrada.

Las comparten todas las implementaciones del repositorio para que se comporten igual.
"""

from collections.abc import Sequence

from .asiento import AsientoContable, Partida
from .enums import EstadoTransaccion
from .errors import MotivoInvalidoError, TransicionInvalidaError
from .puc import CatalogoPUC

ESTADOS_CORREGIBLES = frozenset({EstadoTransaccion.EN_ALERTA, EstadoTransaccion.APROBADA_AUTOMATICA})
# RECHAZADA es final: ninguna acción sale de ella (aprobar, corregir y rechazar la rechazan).
ESTADOS_RECHAZABLES = frozenset({EstadoTransaccion.EN_ALERTA, EstadoTransaccion.APROBADA_AUTOMATICA})
LONGITUD_MINIMA_MOTIVO = 10


def exigir_motivo(motivo: str | None, accion: str) -> str:
    """Corregir y rechazar exigen un motivo de al menos 10 caracteres (sin contar espacios al inicio y final)."""
    texto = (motivo or "").strip()
    if len(texto) < LONGITUD_MINIMA_MOTIVO:
        raise MotivoInvalidoError(
            [f"Para {accion} se exige un motivo de al menos {LONGITUD_MINIMA_MOTIVO} caracteres."]
        )
    return texto


def motivo_opcional(motivo: str | None) -> str | None:
    """Aprobar acepta un motivo opcional; uno vacío o solo de espacios no se guarda."""
    texto = (motivo or "").strip()
    return texto or None


def validar_rechazo(estado: EstadoTransaccion) -> None:
    if estado not in ESTADOS_RECHAZABLES:
        raise TransicionInvalidaError(
            f"Solo se puede rechazar una transacción EN_ALERTA o APROBADA_AUTOMATICA; estado actual: {estado}."
        )


def validar_aprobacion(estado: EstadoTransaccion, tiene_partidas: bool) -> None:
    """Solo se aprueba una transacción EN_ALERTA que ya tenga un asiento propuesto."""
    if estado != EstadoTransaccion.EN_ALERTA:
        raise TransicionInvalidaError(f"Solo se puede aprobar una transacción EN_ALERTA; estado actual: {estado}.")
    if not tiene_partidas:
        raise TransicionInvalidaError(
            "La transacción no tiene asiento propuesto; use corregir para registrar las partidas."
        )


def validar_correccion(
    estado: EstadoTransaccion, partidas: Sequence[Partida], catalogo: CatalogoPUC
) -> AsientoContable:
    """
    Verifica la transición y devuelve el asiento corregido.

    Lanza TransicionInvalidaError, AsientoInvalidoError (partida doble) o
    CuentaNoExisteError (cuenta fuera del catálogo).
    """
    if estado not in ESTADOS_CORREGIBLES:
        raise TransicionInvalidaError(
            f"Solo se puede corregir una transacción EN_ALERTA o APROBADA_AUTOMATICA; estado actual: {estado}."
        )
    asiento = AsientoContable(tuple(partidas))
    for p in asiento.partidas:
        catalogo.obtener(p.cuenta)
    return asiento
