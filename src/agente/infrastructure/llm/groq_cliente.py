"""Cliente de Groq: sugiere cuentas PUC y extrae datos de facturas en texto (PDF)."""

import json

from groq import Groq
from pydantic import BaseModel, Field, ValidationError

from ...config import get_settings


class ErrorGroq(Exception):
    """Falla de configuración o de comunicación con Groq."""


class SugerenciaCuenta(BaseModel):
    cuenta: str
    confianza: float = Field(ge=0, le=1)
    justificacion: str = ""


def _consultar_json(sistema: str, usuario: str) -> dict:
    s = get_settings()
    if not s.groq_api_key:
        raise ErrorGroq("Falta GROQ_API_KEY en el archivo .env")
    try:
        respuesta = Groq(api_key=s.groq_api_key).chat.completions.create(
            model=s.groq_modelo,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": usuario},
            ],
        )
        return json.loads(respuesta.choices[0].message.content)
    except Exception as exc:  # red, cuota, JSON inválido, etc.
        raise ErrorGroq(f"Falla al consultar Groq: {exc}") from exc


def sugerir_cuenta(descripcion: str, candidatas: dict[str, str]) -> SugerenciaCuenta | None:
    """Elige UNA cuenta entre las candidatas del catálogo. Devuelve None si la respuesta no es válida."""
    lista = "\n".join(f"- {codigo}: {nombre}" for codigo, nombre in candidatas.items())
    sistema = (
        "Eres un asistente contable colombiano (PUC, Grupo 2). Clasificas ventas en cuentas de ingreso. "
        "Responde SOLO un JSON con las claves: cuenta (código elegido de la lista), "
        "confianza (0 a 1) y justificacion (una frase). Si ninguna aplica, confianza 0."
    )
    usuario = f"Descripción de la línea facturada: {descripcion}\n\nCuentas permitidas:\n{lista}"
    datos = _consultar_json(sistema, usuario)
    try:
        sugerencia = SugerenciaCuenta.model_validate(datos)
    except ValidationError:
        return None
    # Defensa contra alucinaciones: la cuenta debe estar entre las candidatas.
    return sugerencia if sugerencia.cuenta in candidatas else None


def extraer_factura(texto_pdf: str) -> dict:
    """Convierte el texto de una factura en PDF a un diccionario con los campos de dominio."""
    sistema = (
        "Extraes datos de facturas de venta colombianas. Responde SOLO un JSON con: "
        "numero, fecha (YYYY-MM-DD), tipo_documento (por defecto '01'), moneda (por defecto 'COP'), "
        "emisor {nit, nombre}, adquirente {nit, nombre}, condicion_pago ('CONTADO' o 'CREDITO'), "
        "medio_pago ('10' si es efectivo, o null), lineas [{descripcion, cantidad, valor_linea}], "
        "iva_valor, iva_porcentaje, descuento_global, cargos_globales, subtotal, total_a_pagar. "
        "Los números van sin separador de miles y con punto decimal. Si un dato no existe, usa 0 o null."
    )
    return _consultar_json(sistema, texto_pdf[:12000])