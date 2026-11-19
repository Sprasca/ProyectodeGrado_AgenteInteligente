"""Reglas de revisión del contador sobre una transacción ya registrada.

Las comparten todas las implementaciones del repositorio para que se comporten igual.
"""

from collections.abc import Sequence

from .asiento import AsientoContable, Partida
from .enums import EstadoTransaccion
from .errors import TransicionInvalidaError
from .puc import CatalogoPUC

ESTADOS_CORREGIBLES = frozenset({EstadoTransaccion.EN_ALERTA, EstadoTransaccion.APROBADA_AUTOMATICA})


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
