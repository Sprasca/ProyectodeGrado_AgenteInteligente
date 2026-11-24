"""Lee una factura en PDF (con texto embebido) de forma local y construye una Factura de dominio.

No usa LLM. Extrae texto con pypdf y aplica heurísticas ligeras para rellenar campos.
Los campos que no se puedan determinar se completan con valores por defecto seguros.
La clasificación posterior se hace con el motor de reglas (y fallback NLP opcional).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader

from ...domain.enums import CondicionPago
from ...domain.errors import ErrorDeDominio
from ...domain.factura import ORIGEN_PDF, Factura, Impuesto, LineaFactura, Tercero, numero_provisional


class LectorPDFError(ErrorDeDominio):
    """El PDF no pudo interpretarse como factura."""


_RE_NIT = re.compile(r"\b(\d{9,10}(?:-\d)?)\b")
_RE_FECHA = re.compile(
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"
)
_RE_MONTO = re.compile(
    r"(?:\$\s*)?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+[.,]\d{2})\b"
)
# La palabra clave debe ser palabra completa: sin \b y (?![a-z]) "Fecha" daba el número "cha".
_RE_NUMERO_FACTURA = re.compile(
    r"\b(?:factura|fv|fe|número|numero|n[°o\.])(?![a-z])\s*[:#]?\s*([A-Z0-9][-A-Z0-9/]{2,20})",
    re.IGNORECASE,
)
# \b evita que "total" coincida dentro de "Subtotal".
_RE_TOTAL = re.compile(
    r"\b(?:total\s*(?:a\s*pagar)?|valor\s*total|total\s*factura)\s*[:$]?\s*"
    r"(?:\$\s*)?([\d.,]+)",
    re.IGNORECASE,
)
_RE_SUBTOTAL = re.compile(
    r"(?:subtotal|base\s*gravable|valor\s*bruto)\s*[:$]?\s*(?:\$\s*)?([\d.,]+)",
    re.IGNORECASE,
)
_RE_IVA = re.compile(
    r"(?:iva|impuesto)\s*(?:19\s*%?)?\s*[:$]?\s*(?:\$\s*)?([\d.,]+)",
    re.IGNORECASE,
)


def _a_decimal(texto: str) -> Decimal:
    """Convierte cadenas con formato colombiano/US a Decimal."""
    if not texto:
        return Decimal("0")
    t = texto.strip().replace("$", "").replace(" ", "")
    # 1.234.567,89  → 1234567.89
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        # puede ser decimal europeo o miles
        partes = t.split(",")
        if len(partes[-1]) <= 2:
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    try:
        return Decimal(t)
    except InvalidOperation:
        return Decimal("0")


def _parsear_fecha(texto: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(texto.strip(), fmt).date()
        except ValueError:
            continue
    return None


def texto_de_pdf(contenido: bytes) -> str:
    """Extrae texto de todas las páginas. Falla si no hay texto (PDF escaneado)."""
    try:
        lector = PdfReader(BytesIO(contenido))
    except Exception as exc:
        raise LectorPDFError(f"No se pudo abrir el PDF: {exc}") from exc
    partes: list[str] = []
    for pagina in lector.pages:
        t = pagina.extract_text()
        if t:
            partes.append(t)
    texto = "\n".join(partes).strip()
    if not texto:
        raise LectorPDFError(
            "El PDF no tiene texto embebido (posible escaneo). "
            "Use el XML original de la DIAN o un PDF con texto seleccionable."
        )
    return texto


def _extraer_nits(texto: str) -> list[str]:
    return [m.group(1).replace("-", "") for m in _RE_NIT.finditer(texto)]


def _extraer_montos(texto: str) -> list[Decimal]:
    return [_a_decimal(m.group(1)) for m in _RE_MONTO.finditer(texto) if _a_decimal(m.group(1)) > 0]


def factura_desde_pdf(contenido: bytes) -> Factura:
    """
    Construye una Factura a partir del texto de un PDF usando solo heurísticas locales.

    Completa siempre la estructura: campos no detectados reciben valores por defecto.
    La calidad depende de que el PDF tenga texto real (no imagen).
    """
    texto = texto_de_pdf(contenido)
    texto_lower = texto.lower()

    # Número de factura
    m_num = _RE_NUMERO_FACTURA.search(texto)
    numero = m_num.group(1).strip() if m_num else numero_provisional()

    # Fecha
    fecha = date.today()
    for m in _RE_FECHA.finditer(texto):
        f = _parsear_fecha(m.group(1))
        if f and f.year >= 2010:
            fecha = f
            break

    # NITs (primeros dos distintos suelen ser emisor / adquirente)
    nits = _extraer_nits(texto)
    nit_emisor = nits[0] if nits else ""
    nit_adquirente = nits[1] if len(nits) > 1 else ""

    # Totales
    total = Decimal("0")
    m_total = _RE_TOTAL.search(texto)
    if m_total:
        total = _a_decimal(m_total.group(1))

    subtotal = Decimal("0")
    m_sub = _RE_SUBTOTAL.search(texto)
    if m_sub:
        subtotal = _a_decimal(m_sub.group(1))

    iva = Decimal("0")
    m_iva = _RE_IVA.search(texto)
    if m_iva:
        iva = _a_decimal(m_iva.group(1))

    # Si no hay total, intentar el monto más grande del documento
    if total == 0:
        montos = sorted(_extraer_montos(texto), reverse=True)
        if montos:
            total = montos[0]
            if subtotal == 0 and len(montos) > 1:
                subtotal = montos[1]

    if subtotal == 0 and total > 0 and iva > 0:
        subtotal = total - iva
    elif subtotal == 0 and total > 0:
        # asumir IVA 19 % incluido
        subtotal = (total / Decimal("1.19")).quantize(Decimal("0.01"))
        iva = total - subtotal

    # Condición de pago simple
    condicion = CondicionPago.CONTADO
    if any(p in texto_lower for p in ("crédito", "credito", "a crédito", "plazo")):
        condicion = CondicionPago.CREDITO

    medio_pago = "10" if condicion is CondicionPago.CONTADO else None

    # Línea genérica: si no se pueden detectar ítems, una sola línea con el subtotal
    lineas: tuple[LineaFactura, ...] = (
        LineaFactura(
            id="1",
            descripcion="Concepto extraído de PDF (revisar)",
            cantidad=Decimal("1"),
            valor_linea=subtotal if subtotal > 0 else total,
        ),
    )

    impuestos: tuple[Impuesto, ...] = ()
    if iva > 0:
        impuestos = (Impuesto(codigo="01", valor=iva, porcentaje=Decimal("19")),)

    return Factura(
        numero=numero,
        fecha=fecha,
        tipo_documento="01",
        moneda="COP",
        emisor=Tercero(nit=nit_emisor, nombre="Emisor (extraído de PDF)"),
        adquirente=Tercero(nit=nit_adquirente, nombre="Adquirente (extraído de PDF)"),
        condicion_pago=condicion,
        medio_pago=medio_pago,
        lineas=lineas,
        impuestos=impuestos,
        subtotal_lineas=subtotal,
        descuento_global=Decimal("0"),
        cargos_globales=Decimal("0"),
        base_gravable=subtotal,
        total_con_impuestos=total if total > 0 else subtotal + iva,
        total_a_pagar=total if total > 0 else subtotal + iva,
        extras={"origen": ORIGEN_PDF, "texto_extraido_chars": str(len(texto))},
    )
