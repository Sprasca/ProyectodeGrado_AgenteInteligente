"""API del agente."""

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..application.clasificar_con_nlp import catalogo_por_defecto, clasificar_hibrido, procesar_carpeta
from ..domain.asiento import Partida
from ..domain.enums import EstadoTransaccion, Naturaleza
from ..domain.errors import (
    AsientoInvalidoError,
    CuentaNoExisteError,
    FacturaDuplicadaError,
    TransaccionNoEncontradaError,
    TransicionInvalidaError,
)
from ..domain.puc import CatalogoPUC
from ..infrastructure.db.session import fabrica_compartida
from ..infrastructure.db.transacciones import (
    CuentaNoSembradaError,
    RepositorioNoDisponibleError,
    RepositorioSQL,
    RepositorioTransacciones,
)
from ..infrastructure.dian.lector_pdf import factura_desde_pdf
from ..infrastructure.dian.parser import parsear_xml

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


@app.get("/salud")
def salud() -> dict:
    return {"estado": "ok"}


@app.post("/facturas/clasificar")
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


@app.post("/carpeta/procesar")
def clasificar_carpeta(solicitud: SolicitudCarpeta, catalogo: Catalogo):
    try:
        return jsonable_encoder(procesar_carpeta(solicitud.ruta, solicitud.recursivo, solicitud.usar_nlp, catalogo))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/transacciones")
def listar_transacciones(repositorio: Repositorio, estado: EstadoTransaccion | None = None):
    return jsonable_encoder(repositorio.listar(estado))


@app.get("/transacciones/{id_transaccion}")
def obtener_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio):
    transaccion = repositorio.obtener(id_transaccion)
    if transaccion is None:
        raise HTTPException(404, f"No existe la transacción {id_transaccion}.")
    return jsonable_encoder(transaccion)


@app.post("/transacciones/{id_transaccion}/aprobar")
def aprobar_transaccion(id_transaccion: uuid.UUID, repositorio: Repositorio):
    return jsonable_encoder(repositorio.aprobar(id_transaccion))


@app.post("/transacciones/{id_transaccion}/corregir")
def corregir_transaccion(id_transaccion: uuid.UUID, solicitud: SolicitudCorreccion, repositorio: Repositorio):
    partidas = [Partida(p.cuenta, p.naturaleza, p.valor, p.descripcion) for p in solicitud.partidas]
    return jsonable_encoder(repositorio.corregir(id_transaccion, partidas))
