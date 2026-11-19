"""Repositorio de transacciones en memoria. SOLO para pruebas; misma interfaz que RepositorioSQL."""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime

from agente.domain.asiento import Partida
from agente.domain.enums import EstadoTransaccion
from agente.domain.errors import FacturaDuplicadaError, TransaccionNoEncontradaError
from agente.domain.puc import CatalogoPUC
from agente.domain.resultado import ResultadoClasificacion
from agente.domain.transacciones import validar_aprobacion, validar_correccion
from agente.infrastructure.db.transacciones import CuentaNoSembradaError, TransaccionRegistrada, detalle_lineas


class RepositorioMemoria:
    def __init__(self, catalogo: CatalogoPUC, cuentas_en_bd: set[str] | None = None) -> None:
        """cuentas_en_bd simula la tabla cuenta_puc (llave foránea); por defecto, las del catálogo."""
        self._catalogo = catalogo
        self._cuentas_en_bd = cuentas_en_bd if cuentas_en_bd is not None else {c.codigo for c in catalogo.cuentas}
        self._por_id: dict[uuid.UUID, TransaccionRegistrada] = {}

    def _verificar_llave_foranea(self, partidas: Sequence[Partida]) -> None:
        for p in partidas:
            if p.cuenta not in self._cuentas_en_bd:
                raise CuentaNoSembradaError(p.cuenta)

    def guardar(self, resultado: ResultadoClasificacion) -> uuid.UUID:
        factura = resultado.factura
        if any(t.numero_factura == factura.numero for t in self._por_id.values()):
            raise FacturaDuplicadaError(f"La factura {factura.numero} ya fue registrada.")
        if resultado.asiento is not None:
            self._verificar_llave_foranea(resultado.asiento.partidas)
        registro = TransaccionRegistrada(
            id=uuid.uuid4(),
            numero_factura=factura.numero,
            fecha=factura.fecha,
            nit_adquirente=factura.adquirente.nit,
            valor_total=factura.total_a_pagar,
            estado=resultado.estado,
            confianza=resultado.confianza,
            reglas_aplicadas=tuple(resultado.reglas_aplicadas),
            motivos_alerta=tuple(resultado.motivos_alerta),
            detalle_lineas=tuple(detalle_lineas(resultado)),
            partidas=resultado.asiento.partidas if resultado.asiento is not None else (),
            creado_en=datetime.now(UTC),
        )
        self._por_id[registro.id] = registro
        return registro.id

    def obtener(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada | None:
        return self._por_id.get(id_transaccion)

    def listar(self, estado: EstadoTransaccion | None = None) -> list[TransaccionRegistrada]:
        return [t for t in self._por_id.values() if estado is None or t.estado == estado]

    def _cargar(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada:
        t = self._por_id.get(id_transaccion)
        if t is None:
            raise TransaccionNoEncontradaError(f"No existe la transacción {id_transaccion}.")
        return t

    def aprobar(self, id_transaccion: uuid.UUID) -> TransaccionRegistrada:
        t = self._cargar(id_transaccion)
        validar_aprobacion(t.estado, bool(t.partidas))
        self._por_id[t.id] = replace(t, estado=EstadoTransaccion.APROBADA_CONTADOR)
        return self._por_id[t.id]

    def corregir(self, id_transaccion: uuid.UUID, partidas: Sequence[Partida]) -> TransaccionRegistrada:
        t = self._cargar(id_transaccion)
        asiento = validar_correccion(t.estado, partidas, self._catalogo)
        self._verificar_llave_foranea(asiento.partidas)
        self._por_id[t.id] = replace(
            t,
            partidas=asiento.partidas,
            estado=EstadoTransaccion.CORREGIDA_CONTADOR,
            asiento_propuesto=t.asiento_propuesto if t.asiento_propuesto is not None else t.partidas,
            corregido_en=datetime.now(UTC),
        )
        return self._por_id[t.id]
