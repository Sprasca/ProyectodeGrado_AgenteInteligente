"""Lee una factura en PDF (con texto) y la convierte en Factura de dominio usando Groq."""

from datetime import date
from decimal import Decimal
from io import BytesIO

from pypdf import PdfReader

from ...domain.enums import CondicionPago
from ...domain.errors import ErrorDeDominio
from ...domain.factura import Factura, Impuesto, LineaFactura, Tercero
from ..llm.groq_cliente import extraer_factura


class LectorPDFError(ErrorDeDominio):
    """El PDF no pudo interpretarse como factura."""


def _dec(valor) -> Decimal:
    try:
        return Decimal(str(valor if valor not in (None, "") else 0))
    except Exception:
        return Decimal("0")


def texto_de_pdf(contenido: bytes) -> str:
    lector = PdfReader(BytesIO(contenido))
    texto = "\n".join((p.extract_text() or "") for p in lector.pages).strip()
    if not texto:
        raise LectorPDFError("El PDF no tiene texto (posible escaneo). Requiere OCR o el XML original.")
    return texto


def factura_desde_pdf(contenido: bytes) -> Factura:
    d = extraer_factura(texto_de_pdf(contenido))
    try:
        lineas = tuple(
            LineaFactura(
                id=str(i), descripcion=str(l.get("descripcion", "")),
                cantidad=_dec(l.get("cantidad", 1)), valor_linea=_dec(l.get("valor_linea")),
            )
            for i, l in enumerate(d.get("lineas", []), start=1)
        )
        iva = _dec(d.get("iva_valor"))
        impuestos = (Impuesto("01", iva, _dec(d.get("iva_porcentaje")) or None),) if iva else ()
        subtotal = _dec(d.get("subtotal")) or sum((l.valor_linea for l in lineas), Decimal("0"))
        descuento = _dec(d.get("descuento_global"))
        cargos = _dec(d.get("cargos_globales"))
        total = _dec(d.get("total_a_pagar"))
        emisor = d.get("emisor") or {}
        adquirente = d.get("adquirente") or {}
        return Factura(
            numero=str(d.get("numero", "")),
            fecha=date.fromisoformat(str(d.get("fecha", ""))[:10]),
            tipo_documento=str(d.get("tipo_documento") or "01"),
            moneda=str(d.get("moneda") or "COP"),
            emisor=Tercero(str(emisor.get("nit", "")), str(emisor.get("nombre", ""))),
            adquirente=Tercero(str(adquirente.get("nit", "")), str(adquirente.get("nombre", ""))),
            condicion_pago=CondicionPago.CREDITO if str(d.get("condicion_pago")).upper() == "CREDITO"
            else CondicionPago.CONTADO,
            medio_pago=d.get("medio_pago"),
            lineas=lineas,
            impuestos=impuestos,
            subtotal_lineas=subtotal,
            descuento_global=descuento,
            cargos_globales=cargos,
            base_gravable=subtotal - descuento + cargos,
            total_con_impuestos=total,
            total_a_pagar=total,
            extras={"origen": "pdf"},
        )
    except (ValueError, TypeError) as exc:
        raise LectorPDFError(f"No se pudo construir la factura desde el PDF: {exc}") from exc