"""Reporte de transacciones diarias (RF06) y su exportación (RF07): reglas de negocio puras.

- Cada transacción pertenece a una jornada (ver domain.jornada). Un reporte toma las transacciones
  aprobadas (APROBADA_AUTOMATICA, APROBADA_CONTADOR, CORREGIDA_CONTADOR) de SU jornada que aún no tengan
  reporte. Las RECHAZADAS no entran.
- Hasta 5 reportes vigentes por jornada: como máximo 4 PARCIALES (a mano, durante el día) y un CIERRE,
  con corte a la HORA_CIERRE. El quinto cupo es del cierre: el límite nunca impide cerrar el día. Con un
  cierre vigente, la jornada no admite más reportes salvo que ese cierre se anule. Los ANULADOS no cuentan.
- Una transacción EN_ALERTA de la jornada bloquea cualquier consolidación de esa jornada (RF06 1.a).
- Los movimientos se agrupan por el código registrado en la partida (cuenta de 4 dígitos o
  subcuenta de 6), con subtotales por cuenta de 4 dígitos; débitos y créditos deben cuadrar (RF06 5, 3.a).
- Estados: CONSOLIDADO → EXPORTADO (final) o CONSOLIDADO → ANULADO (final, ampliación del manual).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Protocol

from .asiento import Partida
from .enums import EstadoReporte, EstadoTransaccion, Naturaleza, TipoReporte
from .errors import (
    ConsolidacionBloqueadaError,
    ConsolidacionInconsistenteError,
    JornadaCerradaError,
    LimiteDeReportesError,
    SinTransaccionesError,
    TransicionInvalidaError,
)
from .jornada import Horario, corte_de_la_jornada, jornada_de

ESTADOS_CONSOLIDABLES = frozenset(
    {
        EstadoTransaccion.APROBADA_AUTOMATICA,
        EstadoTransaccion.APROBADA_CONTADOR,
        EstadoTransaccion.CORREGIDA_CONTADOR,
    }
)
LONGITUD_CUENTA = 4
CERO = Decimal("0.00")
MAXIMO_PARCIALES = 4
CUPO_CIERRE = 5
MAXIMO_REPORTES = CUPO_CIERRE


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
    tipo: TipoReporte
    numero: int  # consecutivo dentro de la jornada (1, 2, ...), también cuenta los anulados
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


@dataclass(frozen=True)
class ResumenJornada:
    """Lo que la jornada lleva: reportes vigentes, alertas y transacciones aprobadas sin reporte."""

    fecha_jornada: date
    cupos_ocupados: frozenset[int]  # de los reportes vigentes (1 a 4 parciales, 5 el cierre)
    cierre_id: uuid.UUID | None  # el reporte de cierre vigente
    ultimo_numero: int  # el mayor número usado en la jornada, contando los anulados (0 si no hay)
    alertas_pendientes: int
    transacciones_sin_reporte: int

    @property
    def parciales_vigentes(self) -> int:
        return len(self.cupos_ocupados - {CUPO_CIERRE})

    @property
    def reportes_vigentes(self) -> int:
        return len(self.cupos_ocupados)


def nombre_archivo_reporte(reporte: ReporteDiario) -> str:
    return (
        f"reporte-diario-{reporte.fecha_jornada.isoformat()}-{reporte.numero}-{reporte.tipo.value.lower()}-"
        f"{reporte.id.hex[:8]}.xml"
    )


# --- Qué reporte admite la jornada ------------------------------------------------------------------------


def validar_nuevo_reporte(resumen: ResumenJornada, tipo: TipoReporte) -> int:
    """
    Comprueba que la jornada admita un reporte de ese tipo y devuelve el cupo que ocupará.
    - JornadaCerradaError si ya tiene un cierre vigente.
    - LimiteDeReportesError si es un PARCIAL y ya hay 4 vigentes (el quinto cupo es del cierre).
    - ConsolidacionBloqueadaError si hay transacciones EN_ALERTA en la jornada.
    """
    if resumen.cierre_id is not None:
        raise JornadaCerradaError(
            f"La jornada {resumen.fecha_jornada} ya tiene el reporte de cierre {resumen.cierre_id}: no admite más "
            "reportes salvo que ese cierre se anule."
        )
    if tipo is TipoReporte.CIERRE:
        cupo = CUPO_CIERRE
    else:
        libres = [c for c in range(1, MAXIMO_PARCIALES + 1) if c not in resumen.cupos_ocupados]
        if not libres:
            raise LimiteDeReportesError(
                f"La jornada {resumen.fecha_jornada} ya tiene {MAXIMO_PARCIALES} reportes parciales vigentes: el "
                "quinto cupo es del reporte de cierre. Consolide el cierre a la hora de cierre o anule un parcial."
            )
        cupo = libres[0]
    if resumen.alertas_pendientes:
        raise ConsolidacionBloqueadaError(resumen.alertas_pendientes)
    return cupo


def exigir_transacciones(tipo: TipoReporte, incluidas: int, parciales_vigentes: int) -> None:
    """
    Un PARCIAL nunca está vacío. El CIERRE puede quedar sin transacciones propias si los parciales vigentes
    ya las tomaron todas (marca la jornada como cerrada), pero no se crea si la jornada no tuvo ninguna.
    """
    if incluidas:
        return
    if tipo is TipoReporte.PARCIAL:
        raise SinTransaccionesError(
            "No hay transacciones aprobadas sin reporte en la jornada: no se genera un reporte vacío."
        )
    if not parciales_vigentes:
        raise SinTransaccionesError(
            "La jornada no tiene transacciones aprobadas ni reportes parciales: no hay nada que cerrar."
        )


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


def validar_anulacion(
    estado: EstadoReporte, tipo: TipoReporte = TipoReporte.CIERRE, cierre_vigente: bool = False
) -> None:
    """
    Ampliación del manual: solo un reporte CONSOLIDADO se anula; un EXPORTADO nunca. Un PARCIAL no se anula
    mientras la jornada tenga un cierre vigente: sus transacciones quedarían en una jornada cerrada.
    """
    if estado is EstadoReporte.EXPORTADO:
        raise TransicionInvalidaError("Un reporte EXPORTADO nunca se anula: el ERP ya recibió su archivo.")
    if estado is not EstadoReporte.CONSOLIDADO:
        raise TransicionInvalidaError(f"Solo se anula un reporte CONSOLIDADO; estado actual: {estado}.")
    if tipo is TipoReporte.PARCIAL and cierre_vigente:
        raise TransicionInvalidaError(
            "No se puede anular un reporte parcial de una jornada que ya tiene reporte de cierre: anule primero el "
            "cierre (si no se ha exportado)."
        )


# --- Estado de la jornada (para la interfaz: habilitar o no los botones) ------------------------------------


@dataclass(frozen=True)
class EstadoJornada:
    fecha_jornada: date
    en_curso: bool  # es la jornada a la que pertenece lo que se recibe ahora
    terminada: bool  # ya pasó su HORA_CIERRE
    hora_apertura: time
    hora_cierre: time
    corte_cierre: datetime  # la HORA_CIERRE de esa jornada, en UTC
    reportes_vigentes: int
    parciales_vigentes: int
    maximo_reportes: int
    tiene_cierre: bool
    cierre_id: uuid.UUID | None
    alertas_pendientes: int
    transacciones_sin_reporte: int
    puede_consolidar_parcial: bool
    puede_cerrar: bool
    puede_consolidar: bool
    motivos: tuple[str, ...]  # por qué no se puede consolidar (vacío si se puede todo lo que aplica)


def estado_de_jornada(resumen: ResumenJornada, horario: Horario, ahora: datetime) -> EstadoJornada:
    en_curso = resumen.fecha_jornada == jornada_de(ahora, horario.hora_cierre)
    corte = corte_de_la_jornada(resumen.fecha_jornada, horario.hora_cierre)
    terminada = ahora >= corte
    motivos: list[str] = []
    if resumen.cierre_id is not None:
        motivos.append("La jornada ya tiene reporte de cierre: no admite más reportes salvo que ese cierre se anule.")
    if resumen.alertas_pendientes:
        motivos.append(
            f"Hay {resumen.alertas_pendientes} transacción(es) en alerta en la jornada: el contador debe resolverlas."
        )
    abierta = resumen.cierre_id is None and not resumen.alertas_pendientes

    puede_parcial = abierta and en_curso
    if en_curso and resumen.parciales_vigentes >= MAXIMO_PARCIALES:
        puede_parcial = False
        motivos.append(f"Ya hay {MAXIMO_PARCIALES} reportes parciales vigentes: el quinto cupo es del cierre.")
    if en_curso and not resumen.transacciones_sin_reporte:
        puede_parcial = False
        motivos.append("No hay transacciones aprobadas sin reporte para un parcial.")
    if not en_curso and not terminada:
        motivos.append("La jornada aún no ha empezado.")

    puede_cerrar = abierta and terminada and bool(resumen.transacciones_sin_reporte or resumen.parciales_vigentes)
    if not terminada:
        motivos.append(f"El cierre se hace desde las {horario.hora_cierre:%H:%M} (hora de Colombia).")
    elif resumen.cierre_id is None and not (resumen.transacciones_sin_reporte or resumen.parciales_vigentes):
        motivos.append("La jornada no tiene transacciones aprobadas ni reportes parciales: no hay nada que cerrar.")

    return EstadoJornada(
        fecha_jornada=resumen.fecha_jornada,
        en_curso=en_curso,
        terminada=terminada,
        hora_apertura=horario.hora_apertura,
        hora_cierre=horario.hora_cierre,
        corte_cierre=corte,
        reportes_vigentes=resumen.reportes_vigentes,
        parciales_vigentes=resumen.parciales_vigentes,
        maximo_reportes=MAXIMO_REPORTES,
        tiene_cierre=resumen.cierre_id is not None,
        cierre_id=resumen.cierre_id,
        alertas_pendientes=resumen.alertas_pendientes,
        transacciones_sin_reporte=resumen.transacciones_sin_reporte,
        puede_consolidar_parcial=puede_parcial,
        puede_cerrar=puede_cerrar,
        puede_consolidar=puede_parcial or puede_cerrar,
        motivos=tuple(motivos),
    )
