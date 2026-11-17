"""API del agente. Ejecutar: uv run uvicorn agente.api.main:app --reload"""

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..application.clasificar_con_nlp import clasificar_hibrido, procesar_carpeta
from ..infrastructure.dian.lector_pdf import factura_desde_pdf
from ..infrastructure.dian.parser import parsear_xml

app = FastAPI(title="Agente inteligente PUC", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SolicitudCarpeta(BaseModel):
    ruta: str | None = None  # si es None se usa DIRECTORIO_FACTURAS del .env
    recursivo: bool = False
    usar_nlp: bool = True


@app.get("/salud")
def salud() -> dict:
    return {"estado": "ok"}


@app.post("/facturas/clasificar")
def clasificar_archivo(archivo: UploadFile = File(...), usar_nlp: bool = True):
    contenido = archivo.file.read()
    sufijo = Path(archivo.filename or "").suffix.lower()
    try:
        if sufijo == ".xml":
            factura = parsear_xml(contenido)
        elif sufijo == ".pdf":
            factura = factura_desde_pdf(contenido)
        else:
            raise HTTPException(415, "Solo se aceptan archivos .xml o .pdf")
        return jsonable_encoder(clasificar_hibrido(factura, usar_nlp=usar_nlp))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/carpeta/procesar")
def clasificar_carpeta(solicitud: SolicitudCarpeta):
    try:
        return jsonable_encoder(procesar_carpeta(solicitud.ruta, solicitud.recursivo, solicitud.usar_nlp))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc