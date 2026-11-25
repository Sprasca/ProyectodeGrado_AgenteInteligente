"""Notificaciones (RF04) en PostgreSQL.

- notificar_en_sesion / atender_en_sesion: dentro de la MISMA transacción de base de datos que el hecho que
  las causa o las resuelve (una transacción en alerta, su aprobación, un reporte...): o queda todo o nada.
- RepositorioNotificacionesSQL.registrar: en su propia transacción, para las causas que son fallas (la
  transacción de negocio se revirtió y no queda nada más que guardar). Si la base no responde, no se puede
  guardar: quien llama deja el ERROR en el log.
- La bandeja: cada usuario solo ve, lee y atiende sus propias copias (otra → NotificacionNoEncontradaError).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from ...domain.enums import ClaseNotificacion, EstadoEnvio, Rol, TipoNotificacion
from ...domain.errors import NotificacionNoEncontradaError
from ...domain.notificaciones import Causa, Notificacion, roles_destinatarios, validar_atencion_manual
from .conexion import transaccion_bd
from .models import EnvioCorreoModel, NotificacionModel, NotificacionUsuarioModel, UsuarioModel

log = logging.getLogger("agente.notificaciones")


def notificar_en_sesion(sesion: Session, causa: Causa, ahora: datetime | None = None) -> int:
    """
    Crea la notificación de la causa para los usuarios activos de los roles destinatarios, o actualiza la que
    ya esté abierta para esa misma causa (mensaje, datos y vuelve a quedar sin leer). Devuelve su id.
    """
    ahora = ahora or datetime.now(UTC)
    especificacion = causa.especificacion
    valores = {
        "tipo": causa.tipo.value,
        "clase": especificacion.clase.value,
        "clave": causa.clave,
        "mensaje": causa.mensaje,
        "datos": causa.datos,
        "transaccion_id": causa.transaccion_id,
        "reporte_id": causa.reporte_id,
        "fecha_jornada": causa.fecha_jornada,
        "creada_en": ahora,
        "actualizada_en": ahora,
    }
    # Una sola abierta por causa: el índice único parcial uq_notificacion_causa_abierta decide.
    fila = sesion.execute(
        insert(NotificacionModel)
        .values(**valores)
        .on_conflict_do_update(
            index_elements=["tipo", "clave"],
            index_where=NotificacionModel.atendida_en.is_(None),
            set_={"mensaje": causa.mensaje, "datos": causa.datos, "actualizada_en": ahora},
        )
        .returning(NotificacionModel.id, literal_column("(xmax = 0)").label("nueva"))
    ).one()
    if not fila.nueva:
        sesion.execute(
            update(NotificacionUsuarioModel)
            .where(NotificacionUsuarioModel.notificacion_id == fila.id)
            .values(leida_en=None)
        )
    destinatarios = roles_destinatarios(causa.tipo)
    usuarios = sesion.execute(
        select(UsuarioModel.id, UsuarioModel.rol, UsuarioModel.email).where(
            UsuarioModel.rol.in_([r.value for r in destinatarios]), UsuarioModel.activo.is_(True)
        )
    ).all()
    if not usuarios:
        log.warning(
            "Notificación %s (%s) sin destinatarios: no hay usuarios activos con esos roles.", causa.tipo, causa.clave
        )
    for usuario_id, rol, email in usuarios:
        sesion.execute(
            insert(NotificacionUsuarioModel)
            .values(notificacion_id=fila.id, usuario_id=usuario_id, informativa=destinatarios[Rol(rol)])
            .on_conflict_do_nothing()
        )
        if especificacion.por_correo and email and correo_activo():
            sesion.execute(
                insert(EnvioCorreoModel)
                .values(
                    notificacion_id=fila.id,
                    usuario_id=usuario_id,
                    destinatario=email,
                    estado=EstadoEnvio.PENDIENTE.value,
                    proximo_intento=ahora,
                    creado_en=ahora,
                )
                .on_conflict_do_nothing()
            )
    return fila.id


def correo_activo() -> bool:
    """El correo es opcional: solo se crean envíos si SMTP está configurado."""
    from ...config import get_settings

    return bool(getattr(get_settings(), "correo_activo", False))


def atender_en_sesion(
    sesion: Session,
    tipos: TipoNotificacion | Iterable[TipoNotificacion],
    clave: str,
    resolucion: str,
    ahora: datetime | None = None,
) -> int:
    """Marca ATENDIDA la notificación abierta de esa causa (la causa se resolvió). Devuelve cuántas marcó."""
    tipos = [tipos] if isinstance(tipos, TipoNotificacion) else list(tipos)
    resultado = sesion.execute(
        update(NotificacionModel)
        .where(
            NotificacionModel.tipo.in_([t.value for t in tipos]),
            NotificacionModel.clave == clave,
            NotificacionModel.atendida_en.is_(None),
        )
        .values(atendida_en=ahora or datetime.now(UTC), resolucion=resolucion)
    )
    return resultado.rowcount


class RepositorioNotificaciones(Protocol):
    def registrar(self, causa: Causa) -> int:
        """notificar_en_sesion en su propia transacción (para las causas que son fallas)."""
        ...

    def atender_causa(self, tipos: TipoNotificacion | Iterable[TipoNotificacion], clave: str, resolucion: str) -> int:
        """atender_en_sesion en su propia transacción."""
        ...

    def existe(self, tipo: TipoNotificacion, clave: str) -> bool:
        """¿Hubo alguna vez una notificación de esa causa (atendida o no)?"""
        ...

    def listar(self, usuario_id: int, *, no_leidas: bool = False, no_atendidas: bool = False) -> list[Notificacion]:
        """Las del usuario, de la más reciente a la más antigua."""
        ...

    def contar_no_leidas(self, usuario_id: int) -> int: ...

    def leer(self, usuario_id: int, id_notificacion: int) -> Notificacion:
        """Marca leída su copia. NotificacionNoEncontradaError si no existe o no es suya."""
        ...

    def leer_todas(self, usuario_id: int) -> int:
        """Marca leídas todas sus copias sin leer. Devuelve cuántas marcó."""
        ...

    def atender(self, usuario_id: int, id_notificacion: int) -> Notificacion:
        """
        Atención a mano, solo de las que no se resuelven solas (TransicionInvalidaError si no;
        NotificacionNoEncontradaError si no es suya).
        """
        ...


def _a_notificacion(n: NotificacionModel, copia: NotificacionUsuarioModel, correo: str | None) -> Notificacion:
    return Notificacion(
        id=n.id,
        tipo=TipoNotificacion(n.tipo),
        clase=ClaseNotificacion(n.clase),
        mensaje=n.mensaje,
        datos=n.datos,
        creada_en=n.creada_en,
        actualizada_en=n.actualizada_en,
        atendida_en=n.atendida_en,
        atendida_por=n.atendida_por,
        resolucion=n.resolucion,
        transaccion_id=n.transaccion_id,
        reporte_id=n.reporte_id,
        fecha_jornada=n.fecha_jornada,
        informativa=copia.informativa,
        leida_en=copia.leida_en,
        correo=EstadoEnvio(correo) if correo else None,
    )


class RepositorioNotificacionesSQL:
    def __init__(self, fabrica_sesiones: sessionmaker[Session]) -> None:
        self._fabrica = fabrica_sesiones

    def registrar(self, causa: Causa) -> int:
        with transaccion_bd(self._fabrica) as sesion:
            return notificar_en_sesion(sesion, causa)

    def atender_causa(self, tipos: TipoNotificacion | Iterable[TipoNotificacion], clave: str, resolucion: str) -> int:
        with transaccion_bd(self._fabrica) as sesion:
            return atender_en_sesion(sesion, tipos, clave, resolucion)

    def existe(self, tipo: TipoNotificacion, clave: str) -> bool:
        with transaccion_bd(self._fabrica) as sesion:
            consulta = select(NotificacionModel.id).where(
                NotificacionModel.tipo == tipo.value, NotificacionModel.clave == clave
            )
            return sesion.scalar(consulta.limit(1)) is not None

    def _consulta(self, usuario_id: int):
        return (
            select(NotificacionModel, NotificacionUsuarioModel, EnvioCorreoModel.estado)
            .join(NotificacionUsuarioModel, NotificacionUsuarioModel.notificacion_id == NotificacionModel.id)
            .outerjoin(
                EnvioCorreoModel,
                (EnvioCorreoModel.notificacion_id == NotificacionModel.id)
                & (EnvioCorreoModel.usuario_id == NotificacionUsuarioModel.usuario_id),
            )
            .where(NotificacionUsuarioModel.usuario_id == usuario_id)
        )

    def _una(self, sesion: Session, usuario_id: int, id_notificacion: int) -> Notificacion:
        fila = sesion.execute(self._consulta(usuario_id).where(NotificacionModel.id == id_notificacion)).one_or_none()
        if fila is None:
            raise NotificacionNoEncontradaError(f"No existe la notificación {id_notificacion}.")
        return _a_notificacion(*fila)

    def listar(self, usuario_id: int, *, no_leidas: bool = False, no_atendidas: bool = False) -> list[Notificacion]:
        consulta = self._consulta(usuario_id)
        if no_leidas:
            consulta = consulta.where(NotificacionUsuarioModel.leida_en.is_(None))
        if no_atendidas:
            consulta = consulta.where(NotificacionModel.atendida_en.is_(None))
        consulta = consulta.order_by(NotificacionModel.actualizada_en.desc(), NotificacionModel.id.desc())
        with transaccion_bd(self._fabrica) as sesion:
            return [_a_notificacion(*fila) for fila in sesion.execute(consulta)]

    def contar_no_leidas(self, usuario_id: int) -> int:
        with transaccion_bd(self._fabrica) as sesion:
            return sesion.scalar(
                select(func.count())
                .select_from(NotificacionUsuarioModel)
                .where(NotificacionUsuarioModel.usuario_id == usuario_id, NotificacionUsuarioModel.leida_en.is_(None))
            )

    def leer(self, usuario_id: int, id_notificacion: int) -> Notificacion:
        with transaccion_bd(self._fabrica) as sesion:
            self._una(sesion, usuario_id, id_notificacion)
            sesion.execute(
                update(NotificacionUsuarioModel)
                .where(
                    NotificacionUsuarioModel.notificacion_id == id_notificacion,
                    NotificacionUsuarioModel.usuario_id == usuario_id,
                    NotificacionUsuarioModel.leida_en.is_(None),
                )
                .values(leida_en=datetime.now(UTC))
            )
            return self._una(sesion, usuario_id, id_notificacion)

    def leer_todas(self, usuario_id: int) -> int:
        with transaccion_bd(self._fabrica) as sesion:
            return sesion.execute(
                update(NotificacionUsuarioModel)
                .where(NotificacionUsuarioModel.usuario_id == usuario_id, NotificacionUsuarioModel.leida_en.is_(None))
                .values(leida_en=datetime.now(UTC))
            ).rowcount

    def atender(self, usuario_id: int, id_notificacion: int) -> Notificacion:
        with transaccion_bd(self._fabrica) as sesion:
            actual = self._una(sesion, usuario_id, id_notificacion)
            # FOR UPDATE: dos atenciones simultáneas no la marcan dos veces.
            fila = sesion.get(NotificacionModel, id_notificacion, with_for_update=True)
            validar_atencion_manual(actual.tipo, fila.atendida_en is not None, actual.informativa)
            fila.atendida_en = datetime.now(UTC)
            fila.atendida_por = usuario_id
            fila.resolucion = "Atendida a mano."
            sesion.flush()
            return self._una(sesion, usuario_id, id_notificacion)
