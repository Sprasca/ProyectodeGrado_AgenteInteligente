"""Notificaciones en memoria. SOLO para pruebas; misma interfaz que RepositorioNotificacionesSQL.

notificar / atender_causa hacen lo que notificar_en_sesion / atender_en_sesion hacen dentro de la sesión
de otro repositorio en SQL.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count

from agente.domain.enums import EstadoEnvio, TipoNotificacion
from agente.domain.errors import NotificacionNoEncontradaError
from agente.domain.notificaciones import (
    Causa,
    EnvioPendiente,
    Notificacion,
    roles_destinatarios,
    validar_atencion_manual,
)
from agente.domain.usuarios import Usuario


@dataclass
class _Registro:
    id: int
    causa: Causa
    creada_en: datetime
    actualizada_en: datetime
    atendida_en: datetime | None = None
    atendida_por: int | None = None
    resolucion: str | None = None
    copias: dict[int, dict] = field(default_factory=dict)  # usuario_id → {informativa, leida_en}


class RepositorioNotificacionesMemoria:
    def __init__(self, usuarios: Callable[[], Iterable[Usuario]] = tuple, correo_activo: bool = False) -> None:
        """
        usuarios: los usuarios del sistema (los destinatarios son los activos de cada rol).
        correo_activo: como SMTP configurado en SQL (crea envíos para quien tiene email).
        """
        self._usuarios = usuarios
        self.correo_activo = correo_activo
        self._registros: dict[int, _Registro] = {}
        self._ids = count(1)
        self._envios: dict[int, dict] = {}
        self._ids_envio = count(1)

    # --- lo que en SQL ocurre dentro de la sesión de otro repositorio ----------------------------------------

    def notificar(self, causa: Causa, ahora: datetime | None = None) -> int:
        ahora = ahora or datetime.now(UTC)
        abierta = next(
            (
                r
                for r in self._registros.values()
                if r.atendida_en is None and (r.causa.tipo, r.causa.clave) == (causa.tipo, causa.clave)
            ),
            None,
        )
        if abierta is None:
            abierta = _Registro(next(self._ids), causa, ahora, ahora)
            self._registros[abierta.id] = abierta
        else:
            abierta.causa, abierta.actualizada_en = causa, ahora
            for copia in abierta.copias.values():
                copia["leida_en"] = None
        roles = roles_destinatarios(causa.tipo)
        for usuario in self._usuarios():
            if usuario.activo and usuario.rol in roles:
                abierta.copias.setdefault(usuario.id, {"informativa": roles[usuario.rol], "leida_en": None})
                if self.correo_activo and usuario.email and causa.especificacion.por_correo:
                    self._crear_envio(abierta, usuario, ahora)
        return abierta.id

    def _crear_envio(self, registro: _Registro, usuario: Usuario, ahora: datetime) -> None:
        if any(e["notificacion_id"] == registro.id and e["usuario_id"] == usuario.id for e in self._envios.values()):
            return
        id_ = next(self._ids_envio)
        self._envios[id_] = {
            "id": id_,
            "notificacion_id": registro.id,
            "usuario_id": usuario.id,
            "destinatario": usuario.email,
            "estado": EstadoEnvio.PENDIENTE,
            "intentos": 0,
            "ultimo_error": None,
            "proximo_intento": ahora,
        }

    def atender_causa(self, tipos: TipoNotificacion | Iterable[TipoNotificacion], clave: str, resolucion: str) -> int:
        tipos = {tipos} if isinstance(tipos, TipoNotificacion) else set(tipos)
        marcadas = 0
        for r in self._registros.values():
            if r.atendida_en is None and r.causa.tipo in tipos and r.causa.clave == clave:
                r.atendida_en, r.resolucion = datetime.now(UTC), resolucion
                marcadas += 1
        return marcadas

    # --- RepositorioNotificaciones ----------------------------------------------------------------------------

    def registrar(self, causa: Causa) -> int:
        return self.notificar(causa)

    def existe(self, tipo: TipoNotificacion, clave: str) -> bool:
        return any((r.causa.tipo, r.causa.clave) == (tipo, clave) for r in self._registros.values())

    def _vista(self, r: _Registro, usuario_id: int) -> Notificacion:
        copia = r.copias[usuario_id]
        return Notificacion(
            id=r.id,
            tipo=r.causa.tipo,
            clase=r.causa.especificacion.clase,
            mensaje=r.causa.mensaje,
            datos=r.causa.datos,
            creada_en=r.creada_en,
            actualizada_en=r.actualizada_en,
            atendida_en=r.atendida_en,
            atendida_por=r.atendida_por,
            resolucion=r.resolucion,
            transaccion_id=r.causa.transaccion_id,
            reporte_id=r.causa.reporte_id,
            fecha_jornada=r.causa.fecha_jornada,
            informativa=copia["informativa"],
            leida_en=copia["leida_en"],
            correo=next(
                (
                    e["estado"]
                    for e in self._envios.values()
                    if e["notificacion_id"] == r.id and e["usuario_id"] == usuario_id
                ),
                None,
            ),
        )

    def _cargar(self, usuario_id: int, id_notificacion: int) -> _Registro:
        r = self._registros.get(id_notificacion)
        if r is None or usuario_id not in r.copias:
            raise NotificacionNoEncontradaError(f"No existe la notificación {id_notificacion}.")
        return r

    def listar(self, usuario_id: int, *, no_leidas: bool = False, no_atendidas: bool = False) -> list[Notificacion]:
        propias = [r for r in self._registros.values() if usuario_id in r.copias]
        propias.sort(key=lambda r: (r.actualizada_en, r.id), reverse=True)
        vistas = [self._vista(r, usuario_id) for r in propias]
        return [
            v
            for v in vistas
            if (not no_leidas or v.leida_en is None) and (not no_atendidas or v.atendida_en is None)
        ]

    def contar_no_leidas(self, usuario_id: int) -> int:
        return len(self.listar(usuario_id, no_leidas=True))

    def leer(self, usuario_id: int, id_notificacion: int) -> Notificacion:
        r = self._cargar(usuario_id, id_notificacion)
        r.copias[usuario_id]["leida_en"] = r.copias[usuario_id]["leida_en"] or datetime.now(UTC)
        return self._vista(r, usuario_id)

    def leer_todas(self, usuario_id: int) -> int:
        sin_leer = [
            r for r in self._registros.values() if usuario_id in r.copias and r.copias[usuario_id]["leida_en"] is None
        ]
        for r in sin_leer:
            r.copias[usuario_id]["leida_en"] = datetime.now(UTC)
        return len(sin_leer)

    def atender(self, usuario_id: int, id_notificacion: int) -> Notificacion:
        r = self._cargar(usuario_id, id_notificacion)
        validar_atencion_manual(r.causa.tipo, r.atendida_en is not None, r.copias[usuario_id]["informativa"])
        r.atendida_en, r.atendida_por, r.resolucion = datetime.now(UTC), usuario_id, "Atendida a mano."
        return self._vista(r, usuario_id)

    # --- correo -------------------------------------------------------------------------------------------

    def envios_pendientes(self, ahora: datetime, limite: int = 20) -> list[EnvioPendiente]:
        pendientes = [
            e
            for e in self._envios.values()
            if e["estado"] is EstadoEnvio.PENDIENTE and e["proximo_intento"] <= ahora
        ]
        pendientes.sort(key=lambda e: (e["proximo_intento"], e["id"]))
        return [
            EnvioPendiente(
                e["id"],
                e["notificacion_id"],
                e["usuario_id"],
                e["destinatario"],
                self._registros[e["notificacion_id"]].causa.tipo,
                self._registros[e["notificacion_id"]].causa.mensaje,
                e["intentos"],
            )
            for e in pendientes[:limite]
        ]

    def marcar_enviado(self, id_envio: int, ahora: datetime) -> None:
        envio = self._envios[id_envio]
        envio.update(estado=EstadoEnvio.ENVIADA, intentos=envio["intentos"] + 1, ultimo_error=None)

    def marcar_fallo(self, id_envio: int, error: str, proximo_intento: datetime | None) -> None:
        envio = self._envios[id_envio]
        envio.update(intentos=envio["intentos"] + 1, ultimo_error=error)
        if proximo_intento is None:
            envio["estado"] = EstadoEnvio.FALLIDA
        else:
            envio["proximo_intento"] = proximo_intento

    def envios(self) -> list[dict]:
        """Solo para las pruebas."""
        return list(self._envios.values())

    # --- solo para las pruebas ------------------------------------------------------------------------------

    def de_la_causa(self, tipo: TipoNotificacion, clave: str) -> list[_Registro]:
        return [r for r in self._registros.values() if (r.causa.tipo, r.causa.clave) == (tipo, clave)]

