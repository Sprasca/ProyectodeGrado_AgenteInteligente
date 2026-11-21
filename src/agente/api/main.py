"""API del agente."""

import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from ..domain.enums import EstadoTransaccion, Naturaleza
from ..domain.errors import (
    AsientoInvalidoError,
    ConflictoReglaError,
    CuentaDuplicadaError,
    CuentaNoExisteError,
    DatosInvalidosError,
    FacturaDuplicadaError,
    ReglaNoEncontradaError,
    TransaccionNoEncontradaError,
    TransicionInvalidaError,
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
from ..infrastructure.dian.lector_pdf import factura_desde_pdf
from ..infrastructure.dian.parser import parsear_xml
from .autenticacion import RepoUsuarios, crear_token, usuario_actual, validar_secreto

# Sin JWT_SECRETO (o con uno demasiado corto) la API no arranca.
validar_secreto(get_settings())

# Toda ruta exige un token válido, excepto /salud y /auth/token.
Autenticado = Depends(usuario_actual)

app = FastAPI(title="Agente inteligente PUC", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


@app.exception_handler(DatosInvalidosError)
def _datos_invalidos(_: Request, exc: DatosInvalidosError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "errores": list(exc.errores)})


@app.exception_handler(ConflictoReglaError)
def _conflicto_regla(_: Request, exc: ConflictoReglaError) -> JSONResponse:
    advertencias = [asdict(a) | {"mensaje": str(a)} for a in exc.advertencias]
    return JSONResponse(status_code=409, content={"detail": str(exc), "advertencias": advertencias})


class SolicitudCarpeta(BaseModel):
    ruta: str | None = None  # si es None se usa DIRECTORIO_FACTURAS del .env
    recursivo: bool = False
    usar_nlp: bool = True


class PartidaEntrada(BaseModel):
    cuenta: str
    naturaleza: Naturaleza
    valor: Decimal
    descripcion: str = ""


class SolicitudCorreccion(BaseModel):
    partidas: list[PartidaEntrada]


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


@app.post("/facturas/clasificar", dependencies=[Autenticado])
def clasificar_archivo(
    archivo: Annotated[UploadFile, File()],
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
        respuesta["id"] = str(repositorio.guardar(resultado))
    return respuesta


@app.post("/carpeta/procesar", dependencies=[Autenticado])
def clasificar_carpeta(solicitud: SolicitudCarpeta, catalogo: Catalogo):
    try:
        return jsonable_encoder(procesar_carpeta(solicitud.ruta, solicitud.recursivo, solicitud.usar_nlp, catalogo))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/transacciones", dependencies=[Autenticado])
def listar_transacciones(repositorio: Repositorio, estado: EstadoTransaccion | None = None):
    return jsonable_encoder(repositorio.listar(estado))


@app.get("/transacciones/{id_transaccion}", dependencies=[Autenticado])
def obtener_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio):
    transaccion = repositorio.obtener(id_transaccion)
    if transaccion is None:
        raise HTTPException(404, f"No existe la transacción {id_transaccion}.")
    return jsonable_encoder(transaccion)


@app.post("/transacciones/{id_transaccion}/aprobar", dependencies=[Autenticado])
def aprobar_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio):
    return jsonable_encoder(repositorio.aprobar(id_transaccion))


@app.post("/transacciones/{id_transaccion}/corregir", dependencies=[Autenticado])
def corregir_transaccion(id_transaccion: uuid.UUID, solicitud: SolicitudCorreccion, repositorio: Repositorio):
    partidas = [Partida(p.cuenta, p.naturaleza, p.valor, p.descripcion) for p in solicitud.partidas]
    return jsonable_encoder(repositorio.corregir(id_transaccion, partidas))


# --- Catálogo administrado por el contador (RF09) ------------------------------------------


@app.get("/catalogo/cuentas", dependencies=[Autenticado])
def listar_cuentas(repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.listar_cuentas())


@app.post("/catalogo/cuentas", status_code=status.HTTP_201_CREATED, dependencies=[Autenticado])
def crear_cuenta(cuenta: CuentaEntrada, repositorio: RepoCatalogo):
    """Código repetido → 409. Datos inválidos → 422. Las cuentas no se borran."""
    return jsonable_encoder(repositorio.crear_cuenta(cuenta.codigo, cuenta.nombre, cuenta.naturaleza))


@app.get("/reglas", dependencies=[Autenticado])
def listar_reglas(repositorio: RepoCatalogo, activa: bool | None = None):
    return jsonable_encoder(repositorio.listar_reglas(activa))


@app.get("/reglas/{codigo}", dependencies=[Autenticado])
def obtener_regla(codigo: str, repositorio: RepoCatalogo):
    regla = repositorio.obtener_regla(codigo)
    if regla is None:
        raise HTTPException(404, f"No existe la regla {codigo}.")
    return jsonable_encoder(regla)


@app.post("/reglas", status_code=status.HTTP_201_CREATED, dependencies=[Autenticado])
def crear_regla(regla: ReglaEntrada, repositorio: RepoCatalogo, confirmar: bool = False):
    """Errores → 422. Advertencias de conflicto sin confirmar=true → 409 (no guarda)."""
    nueva = ReglaIngreso(regla.codigo, regla.cuenta_ingreso, tuple(regla.palabras_clave), regla.descripcion)
    return _regla_respuesta(repositorio.crear_regla(nueva, confirmar=confirmar))


@app.put("/reglas/{codigo}", dependencies=[Autenticado])
def editar_regla(codigo: str, regla: ReglaEdicion, repositorio: RepoCatalogo, confirmar: bool = False):
    """Como POST /reglas. El código de una regla no se puede cambiar."""
    if regla.codigo is not None and regla.codigo.strip() != codigo:
        raise HTTPException(422, "El código de una regla no se puede cambiar.")
    guardada = repositorio.editar_regla(
        codigo, regla.cuenta_ingreso, regla.palabras_clave, regla.descripcion, confirmar=confirmar
    )
    return _regla_respuesta(guardada)


@app.post("/reglas/{codigo}/activar", dependencies=[Autenticado])
def activar_regla(codigo: str, repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.activar_regla(codigo))


@app.post("/reglas/{codigo}/desactivar", dependencies=[Autenticado])
def desactivar_regla(codigo: str, repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.desactivar_regla(codigo))


@app.get("/reglas/{codigo}/historial", dependencies=[Autenticado])
def historial_regla(codigo: str, repositorio: RepoCatalogo):
    return jsonable_encoder(repositorio.historial(codigo))
