"""API del agente."""

import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Annotated
from urllib.parse import quote

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict

from ..application.clasificar_con_nlp import (
    catalogo_por_defecto,
    invalidar_catalogo,
    procesar_carpeta,
)
from ..application.registrar_documento import clasificar_documento, guardar_documento
from ..application.reportes import consolidar_reporte, estado_jornada, exportar_reporte
from ..config import get_settings
from ..domain.asiento import Partida
from ..domain.documento import DocumentoOriginal
from ..domain.enums import (
    CanalRecepcion,
    EstadoReporte,
    EstadoTransaccion,
    Naturaleza,
    Rol,
    TipoDocumento,
    TipoReporte,
)
from ..domain.errors import (
    AsientoInvalidoError,
    ConflictoReglaError,
    ConsolidacionBloqueadaError,
    ConsolidacionInconsistenteError,
    CuentaDuplicadaError,
    CuentaNoExisteError,
    DatosInvalidosError,
    ExportacionFallidaError,
    FacturaDuplicadaError,
    NotificacionNoEncontradaError,
    ReglaNoEncontradaError,
    ReporteNoEncontradoError,
    RutaNoPermitidaError,
    SinTransaccionesError,
    TransaccionNoEncontradaError,
    TransicionInvalidaError,
    UltimoAdministradorError,
    UsuarioDuplicadoError,
    UsuarioNoEncontradoError,
)
from ..domain.jornada import Horario, hora_hhmm
from ..domain.notificaciones import Notificacion
from ..domain.puc import CatalogoPUC, ReglaIngreso
from ..infrastructure.db.catalogo import ReglaGuardada, RepositorioCatalogo, RepositorioCatalogoSQL
from ..infrastructure.db.jornada import RepositorioHorario, RepositorioHorarioSQL
from ..infrastructure.db.notificaciones import RepositorioNotificaciones, RepositorioNotificacionesSQL
from ..infrastructure.db.reportes import RepositorioReportes, RepositorioReportesSQL
from ..infrastructure.db.session import fabrica_compartida
from ..infrastructure.db.transacciones import (
    CuentaNoSembradaError,
    RepositorioNoDisponibleError,
    RepositorioSQL,
    RepositorioTransacciones,
)
from ..infrastructure.dian.documentos import tipo_por_nombre
from ..infrastructure.dian.lector_carpeta import carpeta_permitida
from .autenticacion import RepoUsuarios, crear_token, validar_secreto
from .permisos import UsuarioAutorizado, autorizar

# Sin JWT_SECRETO (o con uno demasiado corto) la API no arranca.
validar_secreto(get_settings())

# Toda ruta exige un token válido (401) y el permiso de su rol (403), excepto /salud y /auth/token.
# Los permisos están en agente.api.permisos.
Autorizado = Depends(autorizar)

app = FastAPI(title="Agente inteligente PUC", version="0.3.0")
# CORS solo para los orígenes de CORS_ORIGENES (vacío por defecto: ningún navegador de otro origen).
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origenes,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["Content-Disposition", "X-Contenido-SHA256"],
)


def get_catalogo() -> CatalogoPUC:
    """Catálogo según FUENTE_CATALOGO (en caché). Sin base de datos con "bd" → 503."""
    return catalogo_por_defecto()


Catalogo = Annotated[CatalogoPUC, Depends(get_catalogo)]


def get_repositorio(catalogo: Catalogo) -> RepositorioTransacciones:
    """Repositorio de transacciones. Las pruebas lo reemplazan con app.dependency_overrides."""
    return RepositorioSQL(fabrica_compartida(), catalogo)


Repositorio = Annotated[RepositorioTransacciones, Depends(get_repositorio)]


def get_repositorio_catalogo() -> RepositorioCatalogo:
    """Cuentas y reglas en PostgreSQL. Cada cambio invalida la caché del catálogo de clasificación."""
    return RepositorioCatalogoSQL(fabrica_compartida(), al_cambiar=invalidar_catalogo)


RepoCatalogo = Annotated[RepositorioCatalogo, Depends(get_repositorio_catalogo)]


def get_repositorio_reportes() -> RepositorioReportes:
    """Reportes diarios en PostgreSQL. Las pruebas lo reemplazan con app.dependency_overrides."""
    return RepositorioReportesSQL(fabrica_compartida())


RepoReportes = Annotated[RepositorioReportes, Depends(get_repositorio_reportes)]


def get_repositorio_horario() -> RepositorioHorario:
    """Horario de la jornada en PostgreSQL. Las pruebas lo reemplazan con app.dependency_overrides."""
    return RepositorioHorarioSQL(fabrica_compartida())


RepoHorario = Annotated[RepositorioHorario, Depends(get_repositorio_horario)]


def get_repositorio_notificaciones() -> RepositorioNotificaciones:
    """Notificaciones en PostgreSQL. Las pruebas lo reemplazan con app.dependency_overrides."""
    return RepositorioNotificacionesSQL(fabrica_compartida())


RepoNotificaciones = Annotated[RepositorioNotificaciones, Depends(get_repositorio_notificaciones)]


def _manejador(codigo: int):
    def manejar(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=codigo, content={"detail": str(exc)})

    return manejar


app.add_exception_handler(RepositorioNoDisponibleError, _manejador(503))
app.add_exception_handler(CuentaNoSembradaError, _manejador(409))
app.add_exception_handler(TransaccionNoEncontradaError, _manejador(404))
app.add_exception_handler(FacturaDuplicadaError, _manejador(409))
app.add_exception_handler(TransicionInvalidaError, _manejador(409))
app.add_exception_handler(AsientoInvalidoError, _manejador(422))
app.add_exception_handler(CuentaNoExisteError, _manejador(422))
app.add_exception_handler(CuentaDuplicadaError, _manejador(409))
app.add_exception_handler(ReglaNoEncontradaError, _manejador(404))
app.add_exception_handler(UsuarioDuplicadoError, _manejador(409))
app.add_exception_handler(UsuarioNoEncontradoError, _manejador(404))
app.add_exception_handler(UltimoAdministradorError, _manejador(409))
app.add_exception_handler(RutaNoPermitidaError, _manejador(403))
app.add_exception_handler(ReporteNoEncontradoError, _manejador(404))
app.add_exception_handler(NotificacionNoEncontradaError, _manejador(404))
app.add_exception_handler(SinTransaccionesError, _manejador(409))
# Errores de integridad del sistema (RF06 3.a, RF07 anomalía 4): no los resuelve el usuario.
app.add_exception_handler(ConsolidacionInconsistenteError, _manejador(500))
app.add_exception_handler(ExportacionFallidaError, _manejador(500))


@app.exception_handler(ConsolidacionBloqueadaError)
def _consolidacion_bloqueada(_: Request, exc: ConsolidacionBloqueadaError) -> JSONResponse:
    """RF06 1.a: informa el número de transacciones EN_ALERTA pendientes."""
    return JSONResponse(status_code=409, content={"detail": str(exc), "pendientes": exc.pendientes})


@app.exception_handler(DatosInvalidosError)
def _datos_invalidos(_: Request, exc: DatosInvalidosError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "errores": list(exc.errores)})


@app.exception_handler(ConflictoReglaError)
def _conflicto_regla(_: Request, exc: ConflictoReglaError) -> JSONResponse:
    advertencias = [asdict(a) | {"mensaje": str(a)} for a in exc.advertencias]
    return JSONResponse(status_code=409, content={"detail": str(exc), "advertencias": advertencias})


class SolicitudCarpeta(BaseModel):
    # Relativa a DIRECTORIO_ENTRADA (o absoluta, pero dentro de él). Si es None se usa DIRECTORIO_FACTURAS.
    ruta: str | None = None
    recursivo: bool = False
    usar_nlp: bool = True


class PartidaEntrada(BaseModel):
    cuenta: str
    naturaleza: Naturaleza
    valor: Decimal
    descripcion: str = ""


class SolicitudCorreccion(BaseModel):
    partidas: list[PartidaEntrada]
    motivo: str  # al menos 10 caracteres (si no, 422)


class SolicitudRechazo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str  # al menos 10 caracteres (si no, 422)


class SolicitudAprobacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str | None = None


class SolicitudAnulacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: str  # al menos 10 caracteres (si no, 422)


class CuentaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    nombre: str
    naturaleza: Naturaleza


class ReglaEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    cuenta_ingreso: str
    palabras_clave: list[str]
    descripcion: str = ""


class ReglaEdicion(BaseModel):
    """El código va en la ruta; si se envía en el cuerpo debe ser el mismo (no se puede cambiar)."""

    model_config = ConfigDict(extra="forbid")

    codigo: str | None = None
    cuenta_ingreso: str
    palabras_clave: list[str]
    descripcion: str = ""


class UsuarioEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre_usuario: str
    nombre_completo: str
    contrasena: str
    rol: Rol
    email: str | None = None  # opcional: para recibir las notificaciones por correo


class SolicitudConsolidacion(BaseModel):
    """PARCIAL: la jornada en curso. CIERRE: por defecto la última jornada terminada (o la de fecha_jornada)."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoReporte = TipoReporte.PARCIAL
    fecha_jornada: date | None = None


class HorarioEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hora_apertura: str  # HH:MM, hora de Colombia
    hora_cierre: str


def _horario_respuesta(horario: Horario) -> dict:
    return {
        "hora_apertura": f"{horario.hora_apertura:%H:%M}",
        "hora_cierre": f"{horario.hora_cierre:%H:%M}",
        "zona_horaria": "America/Bogota",
        "actualizado_por": horario.actualizado_por,
        "actualizado_en": horario.actualizado_en,
    }


class UsuarioEdicion(BaseModel):
    """Solo los campos que se envían cambian. Para desactivar: {"activo": false}."""

    model_config = ConfigDict(extra="forbid")

    rol: Rol | None = None
    activo: bool | None = None
    nombre_completo: str | None = None
    email: str | None = None  # "" quita el correo


def _regla_respuesta(guardada: ReglaGuardada) -> dict:
    """La regla guardada y las advertencias que se confirmaron (vacías si no hubo conflicto)."""
    advertencias = [asdict(a) | {"mensaje": str(a)} for a in guardada.advertencias]
    return jsonable_encoder(guardada.regla) | {"advertencias": advertencias}


@app.get("/salud")
def salud() -> dict:
    return {"estado": "ok"}


@app.post("/auth/token")
def emitir_token(formulario: Annotated[OAuth2PasswordRequestForm, Depends()], usuarios: RepoUsuarios) -> dict:
    """OAuth2 con contraseña (campos username y password). 401 si no coinciden o el usuario está inactivo."""
    usuario = usuarios.autenticar(formulario.username, formulario.password)
    if usuario is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Usuario o contraseña incorrectos.", headers={"WWW-Authenticate": "Bearer"}
        )
    minutos = get_settings().jwt_minutos
    return {"access_token": crear_token(usuario), "token_type": "bearer", "expires_in": minutos * 60}


@app.post("/facturas/clasificar")
def clasificar_archivo(
    archivo: Annotated[UploadFile, File()],
    usuario: UsuarioAutorizado,
    repositorio: Repositorio,
    catalogo: Catalogo,
    usar_nlp: bool = True,
    guardar: bool = True,
):
    tipo = tipo_por_nombre(archivo.filename)
    if tipo is None:
        raise HTTPException(415, "Solo se aceptan archivos .xml o .pdf")
    limite = get_settings().tamano_maximo_documento
    contenido = archivo.file.read(limite + 1)  # nunca se lee más de lo permitido
    if len(contenido) > limite:
        raise HTTPException(
            413, f"El archivo supera el tamaño máximo de {get_settings().tamano_maximo_documento_mb} MB."
        )
    try:
        resultado = clasificar_documento(contenido, tipo, catalogo, usar_nlp=usar_nlp)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    respuesta = jsonable_encoder(resultado)
    if guardar:
        documento = DocumentoOriginal.recibido(archivo.filename, tipo, contenido)
        id_transaccion = guardar_documento(
            resultado, documento, repositorio, canal=CanalRecepcion.API, usuario_id=usuario.id
        )
        respuesta["id"] = str(id_transaccion)
    return respuesta


@app.post("/carpeta/procesar", dependencies=[Autorizado])
def clasificar_carpeta(solicitud: SolicitudCarpeta, catalogo: Catalogo):
    """
    Solo carpetas dentro de DIRECTORIO_ENTRADA; fuera (incluso con "..") → 403. Con recursivo=true
    se omiten recibidos/ y no_recibidos/ de la recepción automática.
    """
    settings = get_settings()
    entrada = settings.directorio_entrada
    carpeta = carpeta_permitida(solicitud.ruta, entrada, settings.directorio_facturas)
    try:
        # Con recursivo=true no se reprocesa lo que la recepción automática ya movió.
        excluir = (settings.directorio_recibidos, settings.directorio_no_recibidos)
        resultados = procesar_carpeta(
            str(carpeta), solicitud.recursivo, solicitud.usar_nlp, catalogo, raiz=entrada, excluir=excluir
        )
        return jsonable_encoder(resultados)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/transacciones", dependencies=[Autorizado])
def listar_transacciones(repositorio: Repositorio, estado: EstadoTransaccion | None = None):
    return jsonable_encoder(repositorio.listar(estado))


@app.get("/transacciones/{id_transaccion}", dependencies=[Autorizado])
def obtener_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio):
    transaccion = repositorio.obtener(id_transaccion)
    if transaccion is None:
        raise HTTPException(404, f"No existe la transacción {id_transaccion}.")
    return jsonable_encoder(transaccion)


_TIPO_MIME = {TipoDocumento.XML: "application/xml", TipoDocumento.PDF: "application/pdf"}


@app.get("/transacciones/{id_transaccion}/documento", dependencies=[Autorizado])
def documento_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio) -> Response:
    """El archivo original tal como se recibió. Su SHA-256 va en la cabecera X-Contenido-SHA256."""
    documento = repositorio.documento(id_transaccion)
    if documento is None:
        raise HTTPException(404, f"La transacción {id_transaccion} no tiene documento original guardado.")
    ascii_seguro = documento.nombre_archivo.encode("ascii", "ignore").decode().replace('"', "") or "documento"
    disposicion = f'attachment; filename="{ascii_seguro}"; filename*=UTF-8\'\'{quote(documento.nombre_archivo)}'
    return Response(
        content=documento.contenido,
        media_type=_TIPO_MIME[documento.tipo],
        headers={
            "Content-Disposition": disposicion,
            "X-Contenido-SHA256": documento.sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/transacciones/{id_transaccion}/eventos", dependencies=[Autorizado])
def eventos_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio):
    """Trazabilidad: quién hizo qué y cuándo, del evento más antiguo al más reciente."""
    return jsonable_encoder(repositorio.eventos(id_transaccion))


@app.post("/transacciones/{id_transaccion}/aprobar")
def aprobar_transaccion(
    id_transaccion: uuid.UUID,
    usuario: UsuarioAutorizado,
    repositorio: Repositorio,
    solicitud: Annotated[SolicitudAprobacion | None, Body()] = None,
):
    """El cuerpo es opcional: {"motivo": "..."}."""
    motivo = solicitud.motivo if solicitud is not None else None
    return jsonable_encoder(repositorio.aprobar(id_transaccion, usuario_id=usuario.id, motivo=motivo))


@app.post("/transacciones/{id_transaccion}/corregir")
def corregir_transaccion(
    id_transaccion: uuid.UUID, solicitud: SolicitudCorreccion, usuario: UsuarioAutorizado, repositorio: Repositorio
):
    partidas = [Partida(p.cuenta, p.naturaleza, p.valor, p.descripcion) for p in solicitud.partidas]
    return jsonable_encoder(
        repositorio.corregir(id_transaccion, partidas, motivo=solicitud.motivo, usuario_id=usuario.id)
    )


@app.post("/transacciones/{id_transaccion}/rechazar")
def rechazar_transaccion(
    id_transaccion: uuid.UUID, solicitud: SolicitudRechazo, usuario: UsuarioAutorizado, repositorio: Repositorio
):
    """EN_ALERTA o APROBADA_AUTOMATICA → RECHAZADA (final). Motivo de al menos 10 caracteres."""
    return jsonable_encoder(repositorio.rechazar(id_transaccion, motivo=solicitud.motivo, usuario_id=usuario.id))


# --- Catálogo administrado por el contador (RF09) ------------------------------------------


@app.get("/catalogo/cuentas", dependencies=[Autorizado])
def listar_cuentas(repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.listar_cuentas())


@app.post("/catalogo/cuentas", status_code=status.HTTP_201_CREATED, dependencies=[Autorizado])
def crear_cuenta(cuenta: CuentaEntrada, repositorio: RepoCatalogo):
    """Código repetido → 409. Datos inválidos → 422. Las cuentas no se borran."""
    return jsonable_encoder(repositorio.crear_cuenta(cuenta.codigo, cuenta.nombre, cuenta.naturaleza))


@app.get("/reglas", dependencies=[Autorizado])
def listar_reglas(repositorio: RepoCatalogo, activa: bool | None = None):
    return jsonable_encoder(repositorio.listar_reglas(activa))


@app.get("/reglas/{codigo}", dependencies=[Autorizado])
def obtener_regla(codigo: str, repositorio: RepoCatalogo):
    regla = repositorio.obtener_regla(codigo)
    if regla is None:
        raise HTTPException(404, f"No existe la regla {codigo}.")
    return jsonable_encoder(regla)


@app.post("/reglas", status_code=status.HTTP_201_CREATED)
def crear_regla(regla: ReglaEntrada, usuario: UsuarioAutorizado, repositorio: RepoCatalogo, confirmar: bool = False):
    """Errores → 422. Advertencias de conflicto sin confirmar=true → 409 (no guarda)."""
    nueva = ReglaIngreso(regla.codigo, regla.cuenta_ingreso, tuple(regla.palabras_clave), regla.descripcion)
    return _regla_respuesta(repositorio.crear_regla(nueva, confirmar=confirmar, usuario=usuario.nombre_usuario))


@app.put("/reglas/{codigo}")
def editar_regla(
    codigo: str, regla: ReglaEdicion, usuario: UsuarioAutorizado, repositorio: RepoCatalogo, confirmar: bool = False
):
    """Como POST /reglas. El código de una regla no se puede cambiar."""
    if regla.codigo is not None and regla.codigo.strip() != codigo:
        raise HTTPException(422, "El código de una regla no se puede cambiar.")
    guardada = repositorio.editar_regla(
        codigo,
        regla.cuenta_ingreso,
        regla.palabras_clave,
        regla.descripcion,
        confirmar=confirmar,
        usuario=usuario.nombre_usuario,
    )
    return _regla_respuesta(guardada)


@app.post("/reglas/{codigo}/activar")
def activar_regla(codigo: str, usuario: UsuarioAutorizado, repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.activar_regla(codigo, usuario=usuario.nombre_usuario))


@app.post("/reglas/{codigo}/desactivar")
def desactivar_regla(codigo: str, usuario: UsuarioAutorizado, repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.desactivar_regla(codigo, usuario=usuario.nombre_usuario))


@app.get("/reglas/{codigo}/historial", dependencies=[Autorizado])
def historial_regla(codigo: str, repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.historial(codigo))


# --- Reportes de transacciones diarias (RF06, RF07) ------------------------------------------


@app.post("/reportes/consolidar", status_code=status.HTTP_201_CREATED)
def consolidar(
    usuario: UsuarioAutorizado,
    reportes: RepoReportes,
    horarios: RepoHorario,
    notificaciones: RepoNotificaciones,
    solicitud: Annotated[SolicitudConsolidacion | None, Body()] = None,
):
    """
    Cuerpo opcional {"tipo": "PARCIAL" | "CIERRE", "fecha_jornada": "AAAA-MM-DD"}; por defecto, un PARCIAL.
    PARCIAL: las transacciones aprobadas sin reporte de la jornada en curso, hasta ahora (máximo 4 vigentes).
    CIERRE: las de la jornada (por defecto, la última terminada), con corte a su HORA_CIERRE; uno solo.
    Alertas en la jornada → 409 con "pendientes". Jornada cerrada, límite de parciales, cierre antes de
    hora o nada que consolidar → 409. Débitos ≠ créditos → 500 (error de integridad).
    """
    solicitud = solicitud or SolicitudConsolidacion()
    reporte = consolidar_reporte(
        reportes,
        horarios.vigente(),
        usuario_id=usuario.id,
        tipo=solicitud.tipo,
        fecha_jornada=solicitud.fecha_jornada,
        notificaciones=notificaciones,
    )
    return jsonable_encoder(reporte)


@app.get("/reportes", dependencies=[Autorizado])
def listar_reportes(reportes: RepoReportes, estado: EstadoReporte | None = None, fecha_jornada: date | None = None):
    return jsonable_encoder(reportes.listar(estado, fecha_jornada))


# Las rutas fijas van antes de /reportes/{id_reporte}, que si no las tomaría como un id.
@app.get("/reportes/jornada", dependencies=[Autorizado])
def jornada(reportes: RepoReportes, horarios: RepoHorario, fecha: date | None = None):
    """
    La jornada (por defecto, la en curso): horario, cuántos reportes lleva, si tiene cierre, alertas
    pendientes y si se puede consolidar un parcial o el cierre (con los motivos si no).
    """
    return jsonable_encoder(estado_jornada(reportes, horarios.vigente(), fecha_jornada=fecha))


@app.get("/reportes/horario", dependencies=[Autorizado])
def consultar_horario(horarios: RepoHorario):
    return _horario_respuesta(horarios.vigente())


@app.get("/reportes/horario/historial", dependencies=[Autorizado])
def historial_horario(horarios: RepoHorario):
    """Todos los horarios con quién los fijó y cuándo, del más reciente al más antiguo."""
    return [_horario_respuesta(h) for h in horarios.historial()]


@app.put("/reportes/horario")
def cambiar_horario(entrada: HorarioEntrada, usuario: UsuarioAutorizado, horarios: RepoHorario):
    """
    Solo el contador. Horas HH:MM de Colombia; la apertura antes del cierre (si no, 422). Un cambio que
    movería la jornada en curso → 409. Las transacciones ya recibidas conservan su jornada.
    """
    apertura = hora_hhmm(entrada.hora_apertura, "La hora de apertura")
    cierre = hora_hhmm(entrada.hora_cierre, "La hora de cierre")
    return _horario_respuesta(horarios.cambiar(apertura, cierre, usuario_id=usuario.id))


@app.get("/reportes/{id_reporte}", dependencies=[Autorizado])
def obtener_reporte(id_reporte: uuid.UUID, reportes: RepoReportes):
    """Totales por cuenta y subcuenta, número de transacciones y valor total (RF07 paso 2)."""
    reporte = reportes.obtener(id_reporte)
    if reporte is None:
        raise HTTPException(404, f"No existe el reporte {id_reporte}.")
    return jsonable_encoder(reporte)


@app.get("/reportes/{id_reporte}/eventos", dependencies=[Autorizado])
def eventos_reporte(id_reporte: uuid.UUID, reportes: RepoReportes):
    """Cambios de estado: quién (null si fue el cierre programado), cuándo y por qué."""
    return jsonable_encoder(reportes.eventos(id_reporte))


@app.post("/reportes/{id_reporte}/exportar")
def exportar(
    id_reporte: uuid.UUID, usuario: UsuarioAutorizado, reportes: RepoReportes, notificaciones: RepoNotificaciones
):
    """
    El contador autoriza y en el mismo paso se genera el XML: CONSOLIDADO → EXPORTADO.
    Ya exportado, anulado o con transacciones no aprobadas → 409. Falla la generación → 500 y sigue CONSOLIDADO.
    """
    exportado = exportar_reporte(reportes, id_reporte, usuario_id=usuario.id, notificaciones=notificaciones)
    return jsonable_encoder(exportado)


@app.get("/reportes/{id_reporte}/xml", dependencies=[Autorizado])
def descargar_xml(id_reporte: uuid.UUID, reportes: RepoReportes) -> Response:
    """El XML exportado, para cargarlo en el ERP. Su SHA-256 va en la cabecera X-Contenido-SHA256."""
    archivo = reportes.archivo(id_reporte)
    if archivo is None:
        raise HTTPException(404, f"El reporte {id_reporte} no ha sido exportado.")
    return Response(
        content=archivo.contenido,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{archivo.nombre}"',
            "X-Contenido-SHA256": archivo.sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/reportes/{id_reporte}/anular")
def anular(id_reporte: uuid.UUID, solicitud: SolicitudAnulacion, usuario: UsuarioAutorizado, reportes: RepoReportes):
    """CONSOLIDADO → ANULADO con motivo (mínimo 10 caracteres). Libera sus transacciones. EXPORTADO → 409."""
    return jsonable_encoder(reportes.anular(id_reporte, motivo=solicitud.motivo, usuario_id=usuario.id))


# --- Notificaciones (RF04): cada usuario solo ve, lee y atiende las suyas ------------------------------


def _notificacion_respuesta(notificacion: Notificacion) -> dict:
    return jsonable_encoder(asdict(notificacion) | {"se_atiende_a_mano": notificacion.se_atiende_a_mano})


@app.get("/notificaciones")
def listar_notificaciones(
    usuario: UsuarioAutorizado, notificaciones: RepoNotificaciones, no_leidas: bool = False, no_atendidas: bool = False
):
    """Las del usuario autenticado, de la más reciente a la más antigua. Filtros opcionales."""
    vistas = notificaciones.listar(usuario.id, no_leidas=no_leidas, no_atendidas=no_atendidas)
    return [_notificacion_respuesta(n) for n in vistas]


@app.get("/notificaciones/no-leidas/total")
def total_no_leidas(usuario: UsuarioAutorizado, notificaciones: RepoNotificaciones) -> dict:
    return {"no_leidas": notificaciones.contar_no_leidas(usuario.id)}


@app.post("/notificaciones/leer-todas")
def leer_todas(usuario: UsuarioAutorizado, notificaciones: RepoNotificaciones) -> dict:
    return {"marcadas": notificaciones.leer_todas(usuario.id)}


@app.post("/notificaciones/{id_notificacion}/leer")
def leer_notificacion(id_notificacion: int, usuario: UsuarioAutorizado, notificaciones: RepoNotificaciones):
    """Marca leída su copia. Una notificación de otro usuario responde 404."""
    return _notificacion_respuesta(notificaciones.leer(usuario.id, id_notificacion))


@app.post("/notificaciones/{id_notificacion}/atender")
def atender_notificacion(id_notificacion: int, usuario: UsuarioAutorizado, notificaciones: RepoNotificaciones):
    """
    Solo las que no se resuelven solas: descuadre, archivo no recibido, factura duplicada, error inesperado y
    correo no enviado. Las demás (transacción en alerta, bloqueo de jornada, reporte disponible, jornada sin
    cierre, exportación fallida, directorio no disponible) responden 409: se atienden resolviendo su causa.
    Una copia informativa (el auxiliar en una transacción en alerta) también responde 409. De otro usuario → 404.
    """
    return _notificacion_respuesta(notificaciones.atender(usuario.id, id_notificacion))


# --- Usuarios (solo ADMIN) -------------------------------------------------------------------


@app.get("/usuarios", dependencies=[Autorizado])
def listar_usuarios(usuarios: RepoUsuarios):
    return jsonable_encoder(usuarios.listar())


@app.post("/usuarios", status_code=status.HTTP_201_CREATED, dependencies=[Autorizado])
def crear_usuario(usuario: UsuarioEntrada, usuarios: RepoUsuarios):
    """Nombre repetido → 409. Datos inválidos (p. ej. contraseña de menos de 8 caracteres) → 422."""
    creado = usuarios.crear(
        usuario.nombre_usuario, usuario.nombre_completo, usuario.contrasena, usuario.rol, email=usuario.email
    )
    return jsonable_encoder(creado)


@app.put("/usuarios/{id_usuario}", dependencies=[Autorizado])
def actualizar_usuario(id_usuario: int, cambios: UsuarioEdicion, usuarios: RepoUsuarios):
    """
    Cambia rol, estado (activo), nombre completo o correo ("email": "" lo quita). Dejar el sistema sin ADMIN
    activo → 409.
    """
    if cambios.rol is None and cambios.activo is None and cambios.nombre_completo is None and cambios.email is None:
        raise HTTPException(422, "Envíe al menos uno de: rol, activo, nombre_completo, email.")
    actualizado = usuarios.actualizar(
        id_usuario,
        rol=cambios.rol,
        activo=cambios.activo,
        nombre_completo=cambios.nombre_completo,
        email=cambios.email,
    )
    return jsonable_encoder(actualizado)
