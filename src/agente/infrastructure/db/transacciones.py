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
from ...domain.documento import DocumentoOriginal
from ...domain.enums import EstadoTransaccion, Naturaleza, TipoDocumento, TipoEvento
from ...domain.errors import FacturaDuplicadaError, TransaccionNoEncontradaError
from ...domain.puc import CatalogoPUC
from ...domain.resultado import ResultadoClasificacion
from ...domain.transacciones import (
    exigir_motivo,
    motivo_opcional,
    validar_aprobacion,
    validar_correccion,
    validar_rechazo,
)
from .models import CuentaPUCModel, DocumentoModel, EventoTransaccionModel, PartidaModel, TransaccionModel


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
    cufe: str | None = None


@dataclass(frozen=True)
class EventoTransaccion:
    """Un hecho de la transacción (no repudio). usuario_id es None en los eventos automáticos."""

    id: int
    transaccion_id: uuid.UUID
    tipo: TipoEvento
    usuario_id: int | None
    fecha: datetime | None
    motivo: str | None = None
    antes: dict[str, Any] | None = None
    despues: dict[str, Any] | None = None


class RepositorioTransacciones(Protocol):
    def guardar(
        self,
        resultado: ResultadoClasificacion,
        *,
        usuario_id: int | None = None,
        documento: DocumentoOriginal | None = None,
    ) -> uuid.UUID:
        """
        Registra el resultado, el documento original y los eventos RECIBIDA (de usuario_id) y
        CLASIFICADA (automático). FacturaDuplicadaError si el número o el CUFE ya existen.
        """
        ...

    def documento(self, id_transaccion: uuid.UUID) -> DocumentoOriginal | None:
        """El archivo original, o None si no se guardó. TransaccionNoEncontradaError si no existe."""
        ...

    def obtener(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada | None: ...

    def listar(self, estado: EstadoTransaccion | None = None) -> list[TransaccionRegistrada]: ...

    def aprobar(
        self, id_transaccion: uuid.UUID, *, usuario_id: int | None = None, motivo: str | None = None
    ) -> TransaccionRegistrada:
        """EN_ALERTA con partidas → APROBADA_CONTADOR, con evento APROBADA. Si no, TransicionInvalidaError."""
        ...

    def corregir(
        self,
        id_transaccion: uuid.UUID,
        partidas: Sequence[Partida],
        *,
        motivo: str,
        usuario_id: int | None = None,
    ) -> TransaccionRegistrada:
        """
        Reemplaza las partidas (validadas) y pasa a CORREGIDA_CONTADOR, con evento CORREGIDA.
        Exige un motivo de al menos 10 caracteres (MotivoInvalidoError).

        Antes guarda en asiento_propuesto las partidas del agente (solo si aún no hay una
        propuesta guardada, para no perder la original) y marca corregido_en.
        """
        ...

    def rechazar(
        self, id_transaccion: uuid.UUID, *, motivo: str, usuario_id: int | None = None
    ) -> TransaccionRegistrada:
        """
        EN_ALERTA o APROBADA_AUTOMATICA → RECHAZADA (estado final), con evento RECHAZADA.
        Exige un motivo de al menos 10 caracteres. Desde otro estado: TransicionInvalidaError.
        """
        ...

    def eventos(self, id_transaccion: uuid.UUID) -> list[EventoTransaccion]:
        """Eventos de la transacción, del más antiguo al más reciente. TransaccionNoEncontradaError si no existe."""
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


def datos_recepcion(resultado: ResultadoClasificacion, documento: DocumentoOriginal | None = None) -> dict[str, Any]:
    """Lo que se recibió: identificación de la factura y huella del archivo original."""
    factura = resultado.factura
    datos: dict[str, Any] = {
        "numero_factura": factura.numero,
        "cufe": factura.cufe,
        "fecha": factura.fecha.isoformat(),
        "nit_emisor": factura.emisor.nit,
        "nit_adquirente": factura.adquirente.nit,
        "valor_total": str(factura.total_a_pagar),
    }
    if documento is not None:
        datos["documento"] = {
            "nombre_archivo": documento.nombre_archivo,
            "tipo": documento.tipo.value,
            "bytes": len(documento.contenido),
            "sha256": documento.sha256,
        }
    return datos


def factura_duplicada(numero: str, cufe: str | None = None) -> FacturaDuplicadaError:
    if cufe is not None:
        return FacturaDuplicadaError(f"La factura con CUFE {cufe} ya fue registrada.")
    return FacturaDuplicadaError(f"La factura {numero} ya fue registrada.")


def datos_clasificacion(resultado: ResultadoClasificacion) -> dict[str, Any]:
    """Lo que decidió el agente al clasificar."""
    return {
        "estado": resultado.estado.value,
        "confianza": str(resultado.confianza),
        "reglas_aplicadas": list(resultado.reglas_aplicadas),
        "motivos_alerta": list(resultado.motivos_alerta),
        "partidas": partidas_a_json(resultado.asiento.partidas) if resultado.asiento is not None else [],
    }


def estado_y_partidas(estado: EstadoTransaccion, partidas: Sequence[Partida]) -> dict[str, Any]:
    return {"estado": EstadoTransaccion(estado).value, "partidas": partidas_a_json(partidas)}


def _evento(
    transaccion_id: uuid.UUID,
    tipo: TipoEvento,
    usuario_id: int | None,
    *,
    motivo: str | None = None,
    antes: dict[str, Any] | None = None,
    despues: dict[str, Any] | None = None,
) -> EventoTransaccionModel:
    return EventoTransaccionModel(
        transaccion_id=transaccion_id,
        tipo=tipo.value,
        usuario_id=usuario_id,
        fecha=datetime.now(UTC),
        motivo=motivo,
        antes=antes,
        despues=despues,
    )


def _a_evento(fila: EventoTransaccionModel) -> EventoTransaccion:
    return EventoTransaccion(
        id=fila.id,
        transaccion_id=fila.transaccion_id,
        tipo=TipoEvento(fila.tipo),
        usuario_id=fila.usuario_id,
        fecha=fila.fecha,
        motivo=fila.motivo,
        antes=fila.antes,
        despues=fila.despues,
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
        cufe=t.cufe,
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

    def guardar(
        self,
        resultado: ResultadoClasificacion,
        *,
        usuario_id: int | None = None,
        documento: DocumentoOriginal | None = None,
    ) -> uuid.UUID:
        factura = resultado.factura
        duplicada = factura_duplicada(factura.numero)
        try:
            with self._transaccion() as sesion:
                existe = sesion.scalar(
                    select(TransaccionModel.id).where(TransaccionModel.numero_factura == factura.numero)
                )
                if existe is not None:
                    raise duplicada
                if factura.cufe is not None and sesion.scalar(
                    select(TransaccionModel.id).where(TransaccionModel.cufe == factura.cufe)
                ):
                    raise factura_duplicada(factura.numero, factura.cufe)
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
                    cufe=factura.cufe,
                )
                if resultado.asiento is not None:
                    t.partidas = [_partida_modelo(p) for p in resultado.asiento.partidas]
                sesion.add(t)
                sesion.flush()
                # En la MISMA transacción que la factura: o queda todo, o no queda nada.
                if documento is not None:
                    sesion.add(
                        DocumentoModel(
                            transaccion_id=t.id,
                            nombre_archivo=documento.nombre_archivo,
                            tipo=documento.tipo.value,
                            contenido=documento.contenido,
                            sha256=documento.sha256,
                            recibido_en=datetime.now(UTC),
                        )
                    )
                recepcion = datos_recepcion(resultado, documento)
                sesion.add(_evento(t.id, TipoEvento.RECIBIDA, usuario_id, despues=recepcion))
                sesion.add(_evento(t.id, TipoEvento.CLASIFICADA, None, despues=datos_clasificacion(resultado)))
                sesion.flush()
                return t.id
        except IntegrityError as exc:
            # Carrera entre dos guardados de la misma factura: la restricción UNIQUE decide.
            if _sqlstate(exc) == _VIOLACION_UNICA:
                restriccion = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                if restriccion == "uq_transaccion_cufe":
                    raise factura_duplicada(factura.numero, factura.cufe) from exc
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

    def aprobar(
        self, id_transaccion: uuid.UUID, *, usuario_id: int | None = None, motivo: str | None = None
    ) -> TransaccionRegistrada:
        motivo = motivo_opcional(motivo)
        with self._transaccion() as sesion:
            t = self._cargar(sesion, id_transaccion)
            validar_aprobacion(EstadoTransaccion(t.estado), bool(t.partidas))
            antes = {"estado": t.estado}
            t.estado = EstadoTransaccion.APROBADA_CONTADOR.value
            sesion.add(
                _evento(t.id, TipoEvento.APROBADA, usuario_id, motivo=motivo, antes=antes, despues={"estado": t.estado})
            )
            sesion.flush()
            return _a_registro(t)

    def corregir(
        self,
        id_transaccion: uuid.UUID,
        partidas: Sequence[Partida],
        *,
        motivo: str,
        usuario_id: int | None = None,
    ) -> TransaccionRegistrada:
        motivo = exigir_motivo(motivo, "corregir")
        try:
            with self._transaccion() as sesion:
                t = self._cargar(sesion, id_transaccion)
                asiento = validar_correccion(EstadoTransaccion(t.estado), partidas, self._catalogo)
                previas = _a_registro(t)
                if t.asiento_propuesto is None:
                    t.asiento_propuesto = partidas_a_json(previas.partidas)
                t.corregido_en = datetime.now(UTC)
                t.partidas = [_partida_modelo(p) for p in asiento.partidas]
                t.estado = EstadoTransaccion.CORREGIDA_CONTADOR.value
                sesion.add(
                    _evento(
                        t.id,
                        TipoEvento.CORREGIDA,
                        usuario_id,
                        motivo=motivo,
                        antes=estado_y_partidas(previas.estado, previas.partidas),
                        despues=estado_y_partidas(EstadoTransaccion.CORREGIDA_CONTADOR, asiento.partidas),
                    )
                )
                sesion.flush()
                return _a_registro(t)
        except IntegrityError as exc:
            self._traducir_llave_foranea(exc, partidas)
            raise

    def rechazar(
        self, id_transaccion: uuid.UUID, *, motivo: str, usuario_id: int | None = None
    ) -> TransaccionRegistrada:
        motivo = exigir_motivo(motivo, "rechazar")
        with self._transaccion() as sesion:
            t = self._cargar(sesion, id_transaccion)
            validar_rechazo(EstadoTransaccion(t.estado))
            antes = {"estado": t.estado}
            t.estado = EstadoTransaccion.RECHAZADA.value
            sesion.add(
                _evento(
                    t.id, TipoEvento.RECHAZADA, usuario_id, motivo=motivo, antes=antes, despues={"estado": t.estado}
                )
            )
            sesion.flush()
            return _a_registro(t)

    def eventos(self, id_transaccion: uuid.UUID) -> list[EventoTransaccion]:
        with self._transaccion() as sesion:
            if sesion.get(TransaccionModel, id_transaccion) is None:
                raise TransaccionNoEncontradaError(f"No existe la transacción {id_transaccion}.")
            filas = sesion.scalars(
                select(EventoTransaccionModel)
                .where(EventoTransaccionModel.transaccion_id == id_transaccion)
                .order_by(EventoTransaccionModel.fecha, EventoTransaccionModel.id)
            )
            return [_a_evento(f) for f in filas]

    def documento(self, id_transaccion: uuid.UUID) -> DocumentoOriginal | None:
        with self._transaccion() as sesion:
            if sesion.get(TransaccionModel, id_transaccion) is None:
                raise TransaccionNoEncontradaError(f"No existe la transacción {id_transaccion}.")
            fila = sesion.get(DocumentoModel, id_transaccion)
            if fila is None:
                return None
            return DocumentoOriginal(fila.nombre_archivo, TipoDocumento(fila.tipo), fila.contenido, fila.recibido_en)
