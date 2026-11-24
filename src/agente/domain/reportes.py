"""Reporte de transacciones diarias (RF06) y su exportación (RF07): reglas de negocio puras.

- Entran las transacciones aprobadas (APROBADA_AUTOMATICA, APROBADA_CONTADOR, CORREGIDA_CONTADOR)
  recibidas hasta el corte que aún no tengan reporte. Las RECHAZADAS no entran.
- Una transacción EN_ALERTA recibida hasta el corte bloquea la consolidación (RF06 1.a).
- Los movimientos se agrupan por el código registrado en la partida (cuenta de 4 dígitos o
  subcuenta de 6), con subtotales por cuenta de 4 dígitos; débitos y créditos deben cuadrar (RF06 5, 3.a).
- Estados: CONSOLIDADO → EXPORTADO (final) o CONSOLIDADO → ANULADO (final, ampliación del manual).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from .asiento import Partida
from .enums import EstadoReporte, EstadoTransaccion, Naturaleza
from .errors import ConsolidacionInconsistenteError, TransicionInvalidaError

ESTADOS_CONSOLIDABLES = frozenset(
    {
        EstadoTransaccion.APROBADA_AUTOMATICA,
        EstadoTransaccion.APROBADA_CONTADOR,
        EstadoTransaccion.CORREGIDA_CONTADOR,
    }
)
LONGITUD_CUENTA = 4
CERO = Decimal("0.00")


class TransaccionConsolidable(Protocol):
    id: uuid.UUID
    numero_factura: str
    valor_total: Decimal
    partidas: tuple[Partida, ...]


@dataclass(frozen=True)
class MovimientoConsolidado:
    """Total de un código del PUC tal como se registró (cuenta o subcuenta)."""

    codigo: str
    cuenta: str  # los 4 primeros dígitos
    debito: Decimal
    credito: Decimal


@dataclass(frozen=True)
class SubtotalCuenta:
    cuenta: str
    debito: Decimal
    credito: Decimal


@dataclass(frozen=True)
class Consolidado:
    movimientos: tuple[MovimientoConsolidado, ...]
    subtotales: tuple[SubtotalCuenta, ...]
    total_debito: Decimal
    total_credito: Decimal
    valor_total: Decimal
    numero_transacciones: int


@dataclass(frozen=True)
class ReporteDiario:
    id: uuid.UUID
    fecha_jornada: date
    corte: datetime
    estado: EstadoReporte
    numero_transacciones: int
    total_debito: Decimal
    total_credito: Decimal
    valor_total: Decimal
    movimientos: tuple[MovimientoConsolidado, ...]
    subtotales: tuple[SubtotalCuenta, ...]
    # Las transacciones que se consolidaron en él (se conservan aunque se anule).
    transacciones: tuple[uuid.UUID, ...]
    creado_en: datetime | None = None
    exportado_en: datetime | None = None
    exportado_por: int | None = None
    archivo_sha256: str | None = None


@dataclass(frozen=True)
class ArchivoExportado:
    """El XML generado al exportar (RF07), con su huella SHA-256."""

    nombre: str
    contenido: bytes
    sha256: str


@dataclass(frozen=True)
class EventoReporte:
    """Un cambio de estado del reporte. usuario_id es None si lo hizo el cierre programado."""

    id: int
    reporte_id: uuid.UUID
    tipo: EstadoReporte
    usuario_id: int | None
    fecha: datetime | None
    motivo: str | None = None
    despues: dict[str, Any] | None = None


def nombre_archivo_reporte(reporte: ReporteDiario) -> str:
    return f"reporte-diario-{reporte.fecha_jornada.isoformat()}-{reporte.id.hex[:8]}.xml"


def cuenta_de(codigo: str) -> str:
    return codigo[:LONGITUD_CUENTA]


def consolidar_movimientos(transacciones: Sequence[TransaccionConsolidable]) -> Consolidado:
    """
    Agrupa las partidas por código y por cuenta de 4 dígitos. Si una transacción no tiene partidas o
    los débitos no igualan a los créditos: ConsolidacionInconsistenteError (error de integridad, RF06 3.a).
    """
    totales: dict[str, list[Decimal]] = {}
    for t in transacciones:
        if not t.partidas:
            raise ConsolidacionInconsistenteError(
                f"La transacción {t.numero_factura} ({t.id}) está aprobada pero no tiene partidas."
            )
        for p in t.partidas:
            debito_credito = totales.setdefault(p.cuenta, [CERO, CERO])
            debito_credito[0 if p.naturaleza is Naturaleza.DEBITO else 1] += p.valor
    movimientos = tuple(
        MovimientoConsolidado(codigo, cuenta_de(codigo), debito, credito)
        for codigo, (debito, credito) in sorted(totales.items())
    )
    total_debito = sum((m.debito for m in movimientos), CERO)
    total_credito = sum((m.credito for m in movimientos), CERO)
    if total_debito != total_credito:
        raise ConsolidacionInconsistenteError(
            f"Los débitos ({total_debito}) no igualan a los créditos ({total_credito}) del consolidado."
        )
    return Consolidado(
        movimientos=movimientos,
        subtotales=subtotales_por_cuenta(movimientos),
        total_debito=total_debito,
        total_credito=total_credito,
        valor_total=sum((t.valor_total for t in transacciones), CERO),
        numero_transacciones=len(transacciones),
    )


def subtotales_por_cuenta(movimientos: Iterable[MovimientoConsolidado]) -> tuple[SubtotalCuenta, ...]:
    por_cuenta: dict[str, list[Decimal]] = {}
    for m in movimientos:
        acumulado = por_cuenta.setdefault(m.cuenta, [CERO, CERO])
        acumulado[0] += m.debito
        acumulado[1] += m.credito
    return tuple(SubtotalCuenta(c, d, k) for c, (d, k) in sorted(por_cuenta.items()))


# --- Serialización de los totales (se guardan en JSON con los valores como texto) -----------------------


def movimientos_a_json(movimientos: Iterable[MovimientoConsolidado]) -> list[dict[str, str]]:
    return [
        {"codigo": m.codigo, "cuenta": m.cuenta, "debito": str(m.debito), "credito": str(m.credito)}
        for m in movimientos
    ]


def movimientos_desde_json(datos: Iterable[dict[str, str]]) -> tuple[MovimientoConsolidado, ...]:
    return tuple(
        MovimientoConsolidado(d["codigo"], d["cuenta"], Decimal(d["debito"]), Decimal(d["credito"])) for d in datos
    )


def subtotales_a_json(subtotales: Iterable[SubtotalCuenta]) -> list[dict[str, str]]:
    return [{"cuenta": s.cuenta, "debito": str(s.debito), "credito": str(s.credito)} for s in subtotales]


def subtotales_desde_json(datos: Iterable[dict[str, str]]) -> tuple[SubtotalCuenta, ...]:
    return tuple(SubtotalCuenta(d["cuenta"], Decimal(d["debito"]), Decimal(d["credito"])) for d in datos)


# --- Transiciones del reporte y de sus transacciones ---------------------------------------------------


def exigir_sin_reporte(reporte_id: uuid.UUID | None, accion: str) -> None:
    """Una transacción con reporte vigente no se corrige ni se rechaza; se libera anulando el reporte."""
    if reporte_id is not None:
        raise TransicionInvalidaError(
            f"No se puede {accion} la transacción: pertenece al reporte {reporte_id}. "
            "Si el reporte está CONSOLIDADO, anúlelo primero; un reporte EXPORTADO no se puede anular."
        )


def validar_exportacion(estado: EstadoReporte) -> None:
    """RF07 paso 4: solo un reporte CONSOLIDADO se exporta, y nunca dos veces."""
    if estado is EstadoReporte.EXPORTADO:
        raise TransicionInvalidaError("El reporte ya fue exportado: un reporte no se exporta dos veces.")
    if estado is not EstadoReporte.CONSOLIDADO:
        raise TransicionInvalidaError(f"Solo se exporta un reporte CONSOLIDADO; estado actual: {estado}.")


def exigir_sin_alertas(estados: Iterable[EstadoTransaccion]) -> None:
    """RF07 paso 4: ninguna transacción del reporte puede estar en alerta (ni fuera de los estados aprobados)."""
    no_aprobadas = [e for e in estados if e not in ESTADOS_CONSOLIDABLES]
    if no_aprobadas:
        raise TransicionInvalidaError(
            f"No se puede exportar: {len(no_aprobadas)} transacción(es) del reporte no están aprobadas "
            f"({', '.join(sorted({e.value for e in no_aprobadas}))})."
        )
