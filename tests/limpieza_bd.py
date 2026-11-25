"""Limpieza de datos de prueba en PostgreSQL respetando las llaves foráneas sin cascada."""

from sqlalchemy import delete, select

from agente.infrastructure.db.models import (
    DocumentoModel,
    EnvioCorreoModel,
    EventoTransaccionModel,
    NotificacionModel,
    NotificacionUsuarioModel,
    TransaccionModel,
)


def borrar_transacciones(sesion, condicion) -> None:
    """
    Borra las transacciones que cumplen la condición y, ANTES, su evidencia (notificaciones, eventos y
    documento): esas llaves foráneas no borran en cascada a propósito (no repudio).
    """
    # Los ids se fijan ANTES de borrar: la condición puede depender de los eventos que se borran.
    ids = list(sesion.scalars(select(TransaccionModel.id).where(condicion)))
    if not ids:
        return
    notificaciones = select(NotificacionModel.id).where(NotificacionModel.transaccion_id.in_(ids))
    sesion.execute(delete(EnvioCorreoModel).where(EnvioCorreoModel.notificacion_id.in_(notificaciones)))
    sesion.execute(delete(NotificacionUsuarioModel).where(NotificacionUsuarioModel.notificacion_id.in_(notificaciones)))
    sesion.execute(delete(NotificacionModel).where(NotificacionModel.transaccion_id.in_(ids)))
    sesion.execute(delete(EventoTransaccionModel).where(EventoTransaccionModel.transaccion_id.in_(ids)))
    sesion.execute(delete(DocumentoModel).where(DocumentoModel.transaccion_id.in_(ids)))
    sesion.execute(delete(TransaccionModel).where(TransaccionModel.id.in_(ids)))
