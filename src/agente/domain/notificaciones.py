"""Notificaciones (RF04): qué causas notifican, a quién y cómo se atienden. Reglas puras.

- Cada causa (una transacción en alerta, un reporte, una jornada, un archivo...) es UNA notificación;
  cada destinatario tiene su copia con su propio estado de lectura. "Atendida" es de la causa.
- Las CONTABLES van al contador y al auxiliar; las TECNICAS, al ADMIN como responsable de mantenimiento.
- La transacción en alerta va al contador para resolverla (RF04) y al auxiliar como aviso informativo
  (RN05): el auxiliar la ve pero no la atiende ni resuelve la transacción.
- Unas se atienden solas cuando su causa se resuelve; las demás se atienden a mano (POST .../atender).
- Mientras una causa siga abierta (no atendida) no se crea otra igual: se actualiza la existente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .enums import ClaseNotificacion, EstadoEnvio, Rol, TipoNotificacion
from .errors import TransicionInvalidaError

C, T = ClaseNotificacion.CONTABLE, ClaseNotificacion.TECNICA
CONTADOR, AUXILIAR, ADMIN = Rol.CONTADOR, Rol.AUXILIAR, Rol.ADMIN


@dataclass(frozen=True)
class Especificacion:
    clase: ClaseNotificacion
    # (rol, informativa): informativa = la ve, pero no le toca resolverla.
    destinatarios: tuple[tuple[Rol, bool], ...]
    se_resuelve_sola: bool  # True: solo se atiende resolviendo la causa (no a mano)
    por_correo: bool = True


ESPECIFICACIONES: dict[TipoNotificacion, Especificacion] = {
    TipoNotificacion.TRANSACCION_EN_ALERTA: Especificacion(C, ((CONTADOR, False), (AUXILIAR, True)), True),
    TipoNotificacion.CONSOLIDACION_BLOQUEADA: Especificacion(C, ((CONTADOR, False),), True),
    TipoNotificacion.REPORTE_DISPONIBLE: Especificacion(C, ((CONTADOR, False), (AUXILIAR, False)), True),
    TipoNotificacion.FACTURA_DUPLICADA: Especificacion(C, ((AUXILIAR, False),), False),
    TipoNotificacion.DESCUADRE: Especificacion(T, ((ADMIN, False),), False),
    TipoNotificacion.EXPORTACION_FALLIDA: Especificacion(T, ((ADMIN, False),), True),
    TipoNotificacion.JORNADA_SIN_CIERRE: Especificacion(T, ((ADMIN, False),), True),
    TipoNotificacion.ARCHIVO_NO_RECIBIDO: Especificacion(T, ((ADMIN, False),), False),
    TipoNotificacion.DIRECTORIO_NO_DISPONIBLE: Especificacion(T, ((ADMIN, False),), True),
    TipoNotificacion.ERROR_RECEPCION: Especificacion(T, ((ADMIN, False),), False),
    # Un correo que no se pudo enviar no se avisa por correo (evita un ciclo): solo en la bandeja.
    TipoNotificacion.CORREO_NO_ENVIADO: Especificacion(T, ((ADMIN, False),), False, por_correo=False),
}


@dataclass(frozen=True)
class Causa:
    """Lo que origina una notificación. (tipo, clave) identifica la causa: no hay dos abiertas iguales."""

    tipo: TipoNotificacion
    clave: str
    mensaje: str
    datos: dict[str, Any] | None = None
    transaccion_id: uuid.UUID | None = None
    reporte_id: uuid.UUID | None = None
    fecha_jornada: date | None = None

    @property
    def especificacion(self) -> Especificacion:
        return ESPECIFICACIONES[self.tipo]


@dataclass(frozen=True)
class Notificacion:
    """La copia de un usuario: la causa más su estado de lectura y el de su correo."""

    id: int
    tipo: TipoNotificacion
    clase: ClaseNotificacion
    mensaje: str
    datos: dict[str, Any] | None
    creada_en: datetime | None
    actualizada_en: datetime | None
    atendida_en: datetime | None
    atendida_por: int | None
    resolucion: str | None
    transaccion_id: uuid.UUID | None
    reporte_id: uuid.UUID | None
    fecha_jornada: date | None
    informativa: bool
    leida_en: datetime | None
    correo: EstadoEnvio | None = None  # None si no se envía por correo

    @property
    def se_atiende_a_mano(self) -> bool:
        return not ESPECIFICACIONES[self.tipo].se_resuelve_sola


def clave_jornada(fecha: date) -> str:
    return f"jornada:{fecha.isoformat()}"


def clave_transaccion(id_transaccion: uuid.UUID) -> str:
    return f"transaccion:{id_transaccion}"


def clave_reporte(id_reporte: uuid.UUID) -> str:
    return f"reporte:{id_reporte}"


def roles_destinatarios(tipo: TipoNotificacion) -> dict[Rol, bool]:
    """{rol: informativa} de los destinatarios de ese tipo."""
    return dict(ESPECIFICACIONES[tipo].destinatarios)


def validar_atencion_manual(tipo: TipoNotificacion, atendida: bool, informativa: bool) -> None:
    """
    Solo se atienden a mano las que no se resuelven solas, y no las ya atendidas. Una copia informativa
    no la atiende quien la recibe (no le toca resolverla). TransicionInvalidaError (409) si no se puede.
    """
    if ESPECIFICACIONES[tipo].se_resuelve_sola:
        raise TransicionInvalidaError(
            f"Una notificación {tipo} no se atiende a mano: queda atendida cuando se resuelve su causa."
        )
    if informativa:
        raise TransicionInvalidaError("Esta notificación es informativa para usted: la atiende su responsable.")
    if atendida:
        raise TransicionInvalidaError("La notificación ya está atendida.")


def resolucion_cierre(id_reporte: uuid.UUID) -> str:
    return f"Se generó el reporte de cierre {id_reporte}."


# --- Causas -----------------------------------------------------------------------------------------------


def causa_jornada_sin_cierre(jornada: date, mensaje: str, datos: dict[str, Any]) -> Causa:
    return Causa(TipoNotificacion.JORNADA_SIN_CIERRE, clave_jornada(jornada), mensaje, datos, fecha_jornada=jornada)
