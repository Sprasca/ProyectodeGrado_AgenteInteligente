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
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from .enums import ClaseNotificacion, EstadoEnvio, Rol, TipoNotificacion
from .errors import TransicionInvalidaError

C, T = ClaseNotificacion.CONTABLE, ClaseNotificacion.TECNICA
CONTADOR, AUXILIAR, ADMIN = Rol.CONTADOR, Rol.AUXILIAR, Rol.ADMIN


class TransaccionEnAlerta(Protocol):
    id: uuid.UUID
    numero_factura: str
    nit_adquirente: str
    valor_total: Decimal
    motivos_alerta: tuple[str, ...]
    detalle_lineas: tuple[dict[str, Any], ...]
    fecha_jornada: date | None


class ReporteNotificable(Protocol):
    id: uuid.UUID
    fecha_jornada: date
    tipo: Any
    numero: int
    numero_transacciones: int
    valor_total: Decimal


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


TITULOS: dict[TipoNotificacion, str] = {
    TipoNotificacion.TRANSACCION_EN_ALERTA: "Transacción en alerta",
    TipoNotificacion.CONSOLIDACION_BLOQUEADA: "Consolidación bloqueada por alertas",
    TipoNotificacion.REPORTE_DISPONIBLE: "Reporte disponible para verificación",
    TipoNotificacion.FACTURA_DUPLICADA: "Factura enviada dos veces",
    TipoNotificacion.DESCUADRE: "Alerta técnica: descuadre de débitos y créditos",
    TipoNotificacion.EXPORTACION_FALLIDA: "Alerta técnica: exportación fallida",
    TipoNotificacion.JORNADA_SIN_CIERRE: "Alerta técnica: jornada sin reporte de cierre",
    TipoNotificacion.ARCHIVO_NO_RECIBIDO: "Alerta técnica: archivo no recibido",
    TipoNotificacion.DIRECTORIO_NO_DISPONIBLE: "Alerta técnica: directorio de entrada no disponible",
    TipoNotificacion.ERROR_RECEPCION: "Alerta técnica: error al recibir un archivo",
    TipoNotificacion.CORREO_NO_ENVIADO: "Alerta técnica: correo no enviado",
}


@dataclass(frozen=True)
class EnvioPendiente:
    """Un correo por enviar: la notificación de un destinatario con correo."""

    id: int
    notificacion_id: int
    usuario_id: int
    destinatario: str
    tipo: TipoNotificacion
    mensaje: str
    intentos: int

    @property
    def asunto(self) -> str:
        return f"[Agente PUC] {TITULOS[self.tipo]}"

    @property
    def cuerpo(self) -> str:
        return f"{self.mensaje}\n\nLa notificación está en su bandeja del Agente PUC (GET /notificaciones)."


def espera_del_reintento(intentos: int, espera_inicial: float) -> float:
    """Espera creciente antes del siguiente intento: inicial, el doble, el cuádruple..."""
    return espera_inicial * 2 ** max(intentos - 1, 0)


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


def causa_transaccion_en_alerta(transaccion: TransaccionEnAlerta) -> Causa:
    """RF04 paso 2: número de la factura, descripción de la venta y motivo de la alerta."""
    descripciones = [d["descripcion"] for d in transaccion.detalle_lineas if d.get("descripcion")]
    motivos = list(transaccion.motivos_alerta) or ["Transacción en alerta."]
    mensaje = (
        f"La factura {transaccion.numero_factura} quedó en alerta. Motivo: {' '.join(motivos)} "
        f"Venta: {'; '.join(descripciones) or '(sin descripción)'}."
    )
    datos = {
        "numero_factura": transaccion.numero_factura,
        "descripcion_venta": descripciones,
        "motivos": motivos,
        "valor_total": str(transaccion.valor_total),
        "nit_adquirente": transaccion.nit_adquirente,
    }
    return Causa(
        TipoNotificacion.TRANSACCION_EN_ALERTA,
        clave_transaccion(transaccion.id),
        mensaje,
        datos,
        transaccion_id=transaccion.id,
        fecha_jornada=transaccion.fecha_jornada,
    )


def resolucion_transaccion(accion: str, usuario_id: int | None) -> str:
    return f"La transacción se {accion} (usuario {usuario_id})."


def causa_consolidacion_bloqueada(jornada: date, pendientes: int, tipo_reporte: str) -> Causa:
    """RF06 1.a: al contador, con el número de transacciones pendientes."""
    mensaje = (
        f"No se pudo consolidar el reporte {tipo_reporte} de la jornada {jornada}: hay {pendientes} "
        "transacción(es) en alerta sin resolver. Resuélvalas (aprobar, corregir o rechazar)."
    )
    datos = {"pendientes": pendientes, "tipo_reporte": tipo_reporte}
    clave = clave_jornada(jornada)
    return Causa(TipoNotificacion.CONSOLIDACION_BLOQUEADA, clave, mensaje, datos, fecha_jornada=jornada)


def causa_reporte_disponible(reporte: ReporteNotificable) -> Causa:
    """RF06 paso 8: al auxiliar y al contador, el reporte está disponible para verificación."""
    mensaje = (
        f"El reporte {reporte.tipo} n.º {reporte.numero} de la jornada {reporte.fecha_jornada} está disponible "
        f"para verificación: {reporte.numero_transacciones} transacción(es), valor total {reporte.valor_total}."
    )
    datos = {
        "tipo_reporte": str(reporte.tipo),
        "numero": reporte.numero,
        "numero_transacciones": reporte.numero_transacciones,
        "valor_total": str(reporte.valor_total),
    }
    return Causa(
        TipoNotificacion.REPORTE_DISPONIBLE,
        clave_reporte(reporte.id),
        mensaje,
        datos,
        reporte_id=reporte.id,
        fecha_jornada=reporte.fecha_jornada,
    )


def causa_descuadre(jornada: date, detalle: str) -> Causa:
    """RF06 3.a: error de integridad, al responsable de mantenimiento."""
    mensaje = f"ALERTA TÉCNICA: la consolidación de la jornada {jornada} se abortó por un error de integridad: "
    mensaje += detalle
    datos = {"detalle": detalle}
    return Causa(TipoNotificacion.DESCUADRE, clave_jornada(jornada), mensaje, datos, fecha_jornada=jornada)


def causa_exportacion_fallida(id_reporte: uuid.UUID, detalle: str) -> Causa:
    """RF07 anomalía 4: el reporte sigue CONSOLIDADO; alerta técnica al responsable de mantenimiento."""
    mensaje = f"ALERTA TÉCNICA: falló la exportación del reporte {id_reporte}: {detalle}"
    return Causa(
        TipoNotificacion.EXPORTACION_FALLIDA,
        clave_reporte(id_reporte),
        mensaje,
        {"detalle": detalle},
        reporte_id=id_reporte,
    )


def resolucion_reporte(estado: str, usuario_id: int | None) -> str:
    return f"El reporte quedó {estado} (usuario {usuario_id})."


def resolucion_jornada_sin_alertas() -> str:
    return "Ya no quedan transacciones en alerta en la jornada."


def resolucion_reporte_consolidado(id_reporte: uuid.UUID) -> str:
    return f"Se consolidó el reporte {id_reporte} de la jornada."


def causa_archivo_no_recibido(registro: dict[str, Any], duplicada: bool) -> Causa:
    """
    RF01 1.a y 2.a: el archivo fue a no_recibidos/. Una factura duplicada no es una falla del sistema sino un
    documento enviado dos veces: es CONTABLE y va al auxiliar. Lo demás es TECNICO, para el ADMIN.
    """
    archivo = registro["archivo"]
    movido_a = registro.get("movido_a")
    clave = f"archivo:{Path(movido_a).name if movido_a else archivo + ':' + str(registro.get('fecha'))}"
    if duplicada:
        existente = registro.get("id_transaccion_existente")
        mensaje = (
            f"La factura del archivo {archivo} ya estaba registrada"
            f"{f' (transacción {existente})' if existente else ''} y no se recibió otra vez: se movió a "
            f"no_recibidos/. Revise si el sistema de facturación la envió dos veces."
        )
        return Causa(TipoNotificacion.FACTURA_DUPLICADA, clave, mensaje, registro)
    destino = movido_a or "(no se pudo mover)"
    mensaje = f"ALERTA TÉCNICA: el archivo {archivo} no se recibió y quedó en {destino}. Motivo: {registro['motivo']}"
    return Causa(TipoNotificacion.ARCHIVO_NO_RECIBIDO, clave, mensaje, registro)


def clave_directorio(directorio: Path | str) -> str:
    return f"directorio:{directorio}"


def causa_directorio_no_disponible(directorio: Path | str) -> Causa:
    """RF01 3.a: queda atendida sola cuando el directorio vuelve."""
    mensaje = (
        f"ALERTA TÉCNICA: el directorio de entrada {directorio} no está disponible o no es accesible. "
        "La recepción se reanudará sola cuando vuelva."
    )
    datos = {"directorio": str(directorio)}
    return Causa(TipoNotificacion.DIRECTORIO_NO_DISPONIBLE, clave_directorio(directorio), mensaje, datos)


def causa_error_recepcion(archivo: str, error: str, fecha: str) -> Causa:
    mensaje = f"ALERTA TÉCNICA: error inesperado al recibir el archivo {archivo}: {error}"
    datos = {"archivo": archivo, "error": error, "fecha": fecha}
    return Causa(TipoNotificacion.ERROR_RECEPCION, f"recepcion:{archivo}:{fecha}", mensaje, datos)


def causa_correo_no_enviado(envio: EnvioPendiente, intentos: int, error: str) -> Causa:
    """RF04 anomalía 2: la notificación sigue en la bandeja del destinatario; se avisa al ADMIN (sin correo)."""
    mensaje = (
        f"ALERTA TÉCNICA: no se pudo enviar por correo a {envio.destinatario} la notificación {envio.notificacion_id} "
        f"({TITULOS[envio.tipo]}) tras {intentos} intento(s): {error}. Sigue visible en la bandeja del destinatario."
    )
    datos = {
        "notificacion_id": envio.notificacion_id,
        "usuario_id": envio.usuario_id,
        "destinatario": envio.destinatario,
        "intentos": intentos,
        "error": error,
    }
    return Causa(TipoNotificacion.CORREO_NO_ENVIADO, f"envio:{envio.id}", mensaje, datos)


def causa_jornada_sin_cierre(jornada: date, mensaje: str, datos: dict[str, Any]) -> Causa:
    return Causa(TipoNotificacion.JORNADA_SIN_CIERRE, clave_jornada(jornada), mensaje, datos, fecha_jornada=jornada)
