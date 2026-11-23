"""API del agente."""

import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
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
    clasificar_hibrido,
    invalidar_catalogo,
    procesar_carpeta,
)
from ..config import get_settings
from ..domain.asiento import Partida
from ..domain.documento import DocumentoOriginal
from ..domain.enums import EstadoTransaccion, Naturaleza, Rol, TipoDocumento
from ..domain.errors import (
    AsientoInvalidoError,
    ConflictoReglaError,
    CuentaDuplicadaError,
    CuentaNoExisteError,
    DatosInvalidosError,
    FacturaDuplicadaError,
    ReglaNoEncontradaError,
    RutaNoPermitidaError,
    TransaccionNoEncontradaError,
    TransicionInvalidaError,
    UltimoAdministradorError,
    UsuarioDuplicadoError,
    UsuarioNoEncontradoError,
)
from ..domain.puc import CatalogoPUC, ReglaIngreso
from ..infrastructure.db.catalogo import ReglaGuardada, RepositorioCatalogo, RepositorioCatalogoSQL
from ..infrastructure.db.session import fabrica_compartida
from ..infrastructure.db.transacciones import (
    CuentaNoSembradaError,
    RepositorioNoDisponibleError,
    RepositorioSQL,
    RepositorioTransacciones,
)
from ..infrastructure.dian.lector_carpeta import carpeta_permitida
from ..infrastructure.dian.lector_pdf import factura_desde_pdf
from ..infrastructure.dian.parser import parsear_xml
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


class UsuarioEdicion(BaseModel):
    """Solo los campos que se envían cambian. Para desactivar: {"activo": false}."""

    model_config = ConfigDict(extra="forbid")

    rol: Rol | None = None
    activo: bool | None = None
    nombre_completo: str | None = None


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
    contenido = archivo.file.read()
    sufijo = Path(archivo.filename or "").suffix.lower()
    if sufijo not in (".xml", ".pdf"):
        raise HTTPException(415, "Solo se aceptan archivos .xml o .pdf")
    try:
        factura = parsear_xml(contenido) if sufijo == ".xml" else factura_desde_pdf(contenido)
        resultado = clasificar_hibrido(factura, catalogo, usar_nlp=usar_nlp)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    respuesta = jsonable_encoder(resultado)
    if guardar:
        tipo = TipoDocumento.XML if sufijo == ".xml" else TipoDocumento.PDF
        documento = DocumentoOriginal.recibido(archivo.filename, tipo, contenido)
        respuesta["id"] = str(repositorio.guardar(resultado, usuario_id=usuario.id, documento=documento))
    return respuesta


@app.post("/carpeta/procesar", dependencies=[Autorizado])
def clasificar_carpeta(solicitud: SolicitudCarpeta, catalogo: Catalogo):
    """Solo carpetas dentro de DIRECTORIO_ENTRADA; fuera (incluso con "..") → 403."""
    settings = get_settings()
    entrada = settings.directorio_entrada
    carpeta = carpeta_permitida(solicitud.ruta, entrada, settings.directorio_facturas)
    try:
        resultados = procesar_carpeta(str(carpeta), solicitud.recursivo, solicitud.usar_nlp, catalogo, raiz=entrada)
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


# --- Usuarios (solo ADMIN) -------------------------------------------------------------------


@app.get("/usuarios", dependencies=[Autorizado])
def listar_usuarios(usuarios: RepoUsuarios):
    return jsonable_encoder(usuarios.listar())


@app.post("/usuarios", status_code=status.HTTP_201_CREATED, dependencies=[Autorizado])
def crear_usuario(usuario: UsuarioEntrada, usuarios: RepoUsuarios):
    """Nombre repetido → 409. Datos inválidos (p. ej. contraseña de menos de 8 caracteres) → 422."""
    creado = usuarios.crear(usuario.nombre_usuario, usuario.nombre_completo, usuario.contrasena, usuario.rol)
    return jsonable_encoder(creado)


@app.put("/usuarios/{id_usuario}", dependencies=[Autorizado])
def actualizar_usuario(id_usuario: int, cambios: UsuarioEdicion, usuarios: RepoUsuarios):
    """Cambia rol, estado (activo) o nombre completo. Dejar el sistema sin ADMIN activo → 409."""
    if cambios.rol is None and cambios.activo is None and cambios.nombre_completo is None:
        raise HTTPException(422, "Envíe al menos uno de: rol, activo, nombre_completo.")
    actualizado = usuarios.actualizar(
        id_usuario, rol=cambios.rol, activo=cambios.activo, nombre_completo=cambios.nombre_completo
    )
    return jsonable_encoder(actualizado)
