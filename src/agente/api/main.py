"""API del agente."""

import uuid
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

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
from ..infrastructure.db.session import crear_sessionmaker
from ..infrastructure.db.transacciones import RepositorioNoDisponibleError, RepositorioSQL, RepositorioTransacciones
from ..infrastructure.dian.lector_pdf import factura_desde_pdf
from ..infrastructure.dian.parser import parsear_xml

app = FastAPI(title="Agente inteligente PUC", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@lru_cache
def _fabrica_sesiones() -> sessionmaker[Session]:
    # Sin tiempo de espera, una base caída deja la petición colgada en vez de responder 503.
    return crear_sessionmaker(connect_timeout=5)


def get_repositorio() -> RepositorioTransacciones:
    """Repositorio de transacciones. Las pruebas lo reemplazan con app.dependency_overrides."""
    return RepositorioSQL(_fabrica_sesiones(), catalogo_por_defecto())


Repositorio = Annotated[RepositorioTransacciones, Depends(get_repositorio)]


def _manejador(codigo: int):
    def manejar(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=codigo, content={"detail": str(exc)})

    return manejar


app.add_exception_handler(RepositorioNoDisponibleError, _manejador(503))
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
    usar_nlp: bool = True,
    guardar: bool = True,
):
    contenido = archivo.file.read()
    sufijo = Path(archivo.filename or "").suffix.lower()
    if sufijo not in (".xml", ".pdf"):
        raise HTTPException(415, "Solo se aceptan archivos .xml o .pdf")
    try:
        factura = parsear_xml(contenido) if sufijo == ".xml" else factura_desde_pdf(contenido)
        resultado = clasificar_hibrido(factura, usar_nlp=usar_nlp)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    respuesta = jsonable_encoder(resultado)
    if guardar:
        respuesta["id"] = str(repositorio.guardar(resultado))
    return respuesta


@app.post("/carpeta/procesar")
def clasificar_carpeta(solicitud: SolicitudCarpeta):
    try:
        return jsonable_encoder(procesar_carpeta(solicitud.ruta, solicitud.recursivo, solicitud.usar_nlp))
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
