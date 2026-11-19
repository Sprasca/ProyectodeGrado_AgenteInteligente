"""Persistencia de los resultados de clasificación (tabla transaccion + partida)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ...domain.asiento import Partida
from ...domain.enums import EstadoTransaccion, Naturaleza
from ...domain.errors import FacturaDuplicadaError, TransaccionNoEncontradaError
from ...domain.puc import CatalogoPUC
from ...domain.resultado import ResultadoClasificacion
from ...domain.transacciones import validar_aprobacion, validar_correccion
from .models import CuentaPUCModel, PartidaModel, TransaccionModel


class RepositorioNoDisponibleError(Exception):
    """No hay conexión con la base de datos."""


class CuentaNoSembradaError(Exception):
    """Una partida usa una cuenta del catálogo (data/puc) que no se ha cargado en cuenta_puc."""

    def __init__(self, cuenta: str) -> None:
        self.cuenta = cuenta
        super().__init__(f"La cuenta {cuenta} no está en la base de datos. Ejecute scripts/sembrar_puc.py.")


_VIOLACION_UNICA = "23505"
_VIOLACION_LLAVE_FORANEA = "23503"


def _sqlstate(exc: IntegrityError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


@dataclass(frozen=True)
class TransaccionRegistrada:
    """Transacción tal como quedó guardada, con sus partidas y el detalle por línea."""

    id: uuid.UUID
    numero_factura: str
    fecha: date
    nit_adquirente: str
    valor_total: Decimal
    estado: EstadoTransaccion
    confianza: Decimal
    reglas_aplicadas: tuple[str, ...]
    motivos_alerta: tuple[str, ...]
    detalle_lineas: tuple[dict[str, Any], ...]
    partidas: tuple[Partida, ...]
    creado_en: datetime | None = None
    # Lo que propuso el agente antes de la corrección del contador (None si nunca se corrigió).
    asiento_propuesto: tuple[Partida, ...] | None = None
    corregido_en: datetime | None = None


class RepositorioTransacciones(Protocol):
    def guardar(self, resultado: ResultadoClasificacion) -> uuid.UUID:
        """Registra el resultado. Lanza FacturaDuplicadaError si el número de factura ya existe."""
        ...

    def obtener(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada | None: ...

    def listar(self, estado: EstadoTransaccion | None = None) -> list[TransaccionRegistrada]: ...

    def aprobar(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada:
        """EN_ALERTA con partidas → APROBADA_CONTADOR. Si no, TransicionInvalidaError."""
        ...

    def corregir(self, id_transaccion: uuid.UUID, partidas: Sequence[Partida]) -> TransaccionRegistrada:
        """
        Reemplaza las partidas (validadas) y pasa a CORREGIDA_CONTADOR.

        Antes guarda en asiento_propuesto las partidas del agente (solo si aún no hay una
        propuesta guardada, para no perder la original) y marca corregido_en.
        """
        ...


def detalle_lineas(resultado: ResultadoClasificacion) -> list[dict[str, Any]]:
    """Serializa ResultadoClasificacion.lineas a JSON para la columna detalle_lineas."""
    return [
        {
            "id_linea": linea.id_linea,
            "descripcion": linea.descripcion,
            "estado": linea.estado.value,
            "cuenta_ingreso": linea.cuenta_ingreso,
            "regla": linea.regla,
            "palabras_encontradas": list(linea.palabras_encontradas),
            "cuentas_candidatas": list(linea.cuentas_candidatas),
        }
        for linea in resultado.lineas
    ]


def partidas_a_json(partidas: Sequence[Partida]) -> list[dict[str, str]]:
    """El valor va como texto para no perder precisión decimal en JSON."""
    return [
        {"cuenta": p.cuenta, "naturaleza": p.naturaleza.value, "valor": str(p.valor), "descripcion": p.descripcion}
        for p in partidas
    ]


def partidas_desde_json(datos: Sequence[dict[str, str]]) -> tuple[Partida, ...]:
    return tuple(
        Partida(d["cuenta"], Naturaleza(d["naturaleza"]), Decimal(d["valor"]), d.get("descripcion", ""))
        for d in datos
    )


def _a_registro(t: TransaccionModel) -> TransaccionRegistrada:
    return TransaccionRegistrada(
        id=t.id,
        numero_factura=t.numero_factura,
        fecha=t.fecha,
        nit_adquirente=t.nit_adquirente,
        valor_total=t.valor_total,
        estado=EstadoTransaccion(t.estado),
        confianza=t.confianza,
        reglas_aplicadas=tuple(t.reglas_aplicadas),
        motivos_alerta=tuple(t.motivos_alerta),
        detalle_lineas=tuple(t.detalle_lineas),
        partidas=tuple(
            Partida(p.cuenta_codigo, Naturaleza(p.naturaleza), p.valor, p.descripcion)
            for p in sorted(t.partidas, key=lambda p: p.id)
        ),
        creado_en=t.creado_en,
        asiento_propuesto=partidas_desde_json(t.asiento_propuesto) if t.asiento_propuesto is not None else None,
        corregido_en=t.corregido_en,
    )


def _partida_modelo(p: Partida) -> PartidaModel:
    return PartidaModel(
        cuenta_codigo=p.cuenta, naturaleza=p.naturaleza.value, valor=p.valor, descripcion=p.descripcion[:200]
    )


class RepositorioSQL:
    """Implementación sobre PostgreSQL con SQLAlchemy."""

    def __init__(self, fabrica_sesiones: sessionmaker[Session], catalogo: CatalogoPUC) -> None:
        self._fabrica = fabrica_sesiones
        self._catalogo = catalogo

    @contextmanager
    def _transaccion(self) -> Iterator[Session]:
        try:
            with self._fabrica() as sesion, sesion.begin():
                yield sesion
        except (OperationalError, InterfaceError) as exc:
            raise RepositorioNoDisponibleError(
                "No hay conexión con PostgreSQL. Verifique que la base esté levantada "
                "(docker compose up -d db) y que DATABASE_URL sea correcta."
            ) from exc

    def _cargar(self, sesion: Session, id_transaccion: uuid.UUID) -> TransaccionModel:
        t = sesion.get(
            TransaccionModel, id_transaccion, options=[selectinload(TransaccionModel.partidas)], with_for_update=True
        )
        if t is None:
            raise TransaccionNoEncontradaError(f"No existe la transacción {id_transaccion}.")
        return t

    def guardar(self, resultado: ResultadoClasificacion) -> uuid.UUID:
        factura = resultado.factura
        duplicada = FacturaDuplicadaError(f"La factura {factura.numero} ya fue registrada.")
        try:
            with self._transaccion() as sesion:
                existe = sesion.scalar(
                    select(TransaccionModel.id).where(TransaccionModel.numero_factura == factura.numero)
                )
                if existe is not None:
                    raise duplicada
                t = TransaccionModel(
                    id=uuid.uuid4(),
                    numero_factura=factura.numero,
                    fecha=factura.fecha,
                    nit_adquirente=factura.adquirente.nit,
                    valor_total=factura.total_a_pagar,
                    estado=resultado.estado.value,
                    confianza=resultado.confianza,
                    reglas_aplicadas=list(resultado.reglas_aplicadas),
                    motivos_alerta=list(resultado.motivos_alerta),
                    detalle_lineas=detalle_lineas(resultado),
                )
                if resultado.asiento is not None:
                    t.partidas = [_partida_modelo(p) for p in resultado.asiento.partidas]
                sesion.add(t)
                sesion.flush()
                return t.id
        except IntegrityError as exc:
            # Carrera entre dos guardados de la misma factura: la restricción UNIQUE decide.
            if _sqlstate(exc) == _VIOLACION_UNICA:
                raise duplicada from exc
            partidas = resultado.asiento.partidas if resultado.asiento is not None else ()
            self._traducir_llave_foranea(exc, partidas)
            raise

    def _traducir_llave_foranea(self, exc: IntegrityError, partidas: Sequence[Partida]) -> None:
        """Si la base rechazó una partida por la llave foránea a cuenta_puc, lanza CuentaNoSembradaError."""
        if _sqlstate(exc) != _VIOLACION_LLAVE_FORANEA or not partidas:
            return
        codigos = list(dict.fromkeys(p.cuenta for p in partidas))
        with self._transaccion() as sesion:
            en_bd = set(sesion.scalars(select(CuentaPUCModel.codigo).where(CuentaPUCModel.codigo.in_(codigos))))
        faltantes = [c for c in codigos if c not in en_bd]
        if faltantes:
            raise CuentaNoSembradaError(faltantes[0]) from exc

    def obtener(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada | None:
        with self._transaccion() as sesion:
            t = sesion.get(TransaccionModel, id_transaccion, options=[selectinload(TransaccionModel.partidas)])
            return _a_registro(t) if t is not None else None

    def listar(self, estado: EstadoTransaccion | None = None) -> list[TransaccionRegistrada]:
        consulta = (
            select(TransaccionModel)
            .options(selectinload(TransaccionModel.partidas))
            .order_by(TransaccionModel.creado_en, TransaccionModel.numero_factura)
        )
        if estado is not None:
            consulta = consulta.where(TransaccionModel.estado == estado.value)
        with self._transaccion() as sesion:
            return [_a_registro(t) for t in sesion.scalars(consulta)]

    def aprobar(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada:
        with self._transaccion() as sesion:
            t = self._cargar(sesion, id_transaccion)
            validar_aprobacion(EstadoTransaccion(t.estado), bool(t.partidas))
            t.estado = EstadoTransaccion.APROBADA_CONTADOR.value
            sesion.flush()
            return _a_registro(t)

    def corregir(self, id_transaccion: uuid.UUID, partidas: Sequence[Partida]) -> TransaccionRegistrada:
        try:
            with self._transaccion() as sesion:
                t = self._cargar(sesion, id_transaccion)
                asiento = validar_correccion(EstadoTransaccion(t.estado), partidas, self._catalogo)
                if t.asiento_propuesto is None:
                    propuestas = _a_registro(t).partidas
                    t.asiento_propuesto = partidas_a_json(propuestas)
                t.corregido_en = datetime.now(UTC)
                t.partidas = [_partida_modelo(p) for p in asiento.partidas]
                t.estado = EstadoTransaccion.CORREGIDA_CONTADOR.value
                sesion.flush()
                return _a_registro(t)
        except IntegrityError as exc:
            self._traducir_llave_foranea(exc, partidas)
            raise
