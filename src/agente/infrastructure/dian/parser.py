"""Parser determinista de facturas electrónicas UBL 2.1 (DIAN) y AttachedDocument.

Extrae los campos necesarios para construir el objeto de dominio Factura.
No intenta validar firma digital ni esquema XSD completo (eso queda fuera del Sprint 1).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from ...domain.enums import CondicionPago
from ...domain.errors import ErrorDeDominio
from ...domain.factura import Factura, Impuesto, LineaFactura, Tercero

# Namespaces habituales en UBL 2.1 Colombia
NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
}


class ParserUBLError(ErrorDeDominio):
    """El XML no pudo interpretarse como factura de venta válida."""


def _texto(nodo: ET.Element | None, ruta: str, default: str = "") -> str:
    if nodo is None:
        return default
    encontrado = nodo.find(ruta, NS)
    return (encontrado.text or "").strip() if encontrado is not None else default


def _decimal(nodo: ET.Element | None, ruta: str, default: str = "0") -> Decimal:
    raw = _texto(nodo, ruta, default)
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal(default)


def _fecha(nodo: ET.Element | None, ruta: str) -> date:
    raw = _texto(nodo, ruta)
    if not raw:
        raise ParserUBLError("Falta cbc:IssueDate en la factura.")
    return date.fromisoformat(raw[:10])


def _tercero(nodo: ET.Element | None) -> Tercero:
    if nodo is None:
        return Tercero(nit="", nombre="")
    nit = _texto(nodo, "cac:PartyTaxScheme/cbc:CompanyID") or _texto(
        nodo, "cac:PartyIdentification/cbc:ID"
    )
    nombre = _texto(nodo, "cac:PartyTaxScheme/cbc:RegistrationName") or _texto(
        nodo, "cac:PartyName/cbc:Name"
    )
    return Tercero(nit=nit, nombre=nombre)


def _extraer_invoice(raiz: ET.Element) -> ET.Element:
    """Devuelve el nodo Invoice aunque venga embebido dentro de AttachedDocument."""
    tag = raiz.tag.split("}")[-1] if "}" in raiz.tag else raiz.tag

    if tag == "Invoice":
        return raiz

    if tag == "AttachedDocument":
        # Busca el Invoice embebido (común en DIAN)
        for desc in raiz.findall(".//cac:Attachment/cac:ExternalReference/cbc:Description", NS):
            if desc.text and "<Invoice" in desc.text:
                try:
                    embebido = ET.fromstring(desc.text)
                    return embebido
                except ET.ParseError as exc:
                    raise ParserUBLError(f"Invoice embebido mal formado: {exc}") from exc

        # Alternativa: CDATA o base64 (simplificado: solo texto)
        for attached in raiz.findall(".//cac:Attachment", NS):
            for child in attached.iter():
                if child.text and "<Invoice" in (child.text or ""):
                    try:
                        return ET.fromstring(child.text)
                    except ET.ParseError:
                        continue

        raise ParserUBLError("AttachedDocument no contiene un Invoice embebido reconocible.")

    raise ParserUBLError(f"Documento raíz no soportado: {tag}")


def parsear_xml(contenido: str | bytes) -> Factura:
    """
    Convierte un XML UBL 2.1 (Invoice o AttachedDocument) en el objeto de dominio Factura.
    """
    if isinstance(contenido, bytes):
        contenido = contenido.decode("utf-8")

    try:
        raiz = ET.fromstring(contenido)
    except ET.ParseError as exc:
        raise ParserUBLError(f"XML inválido: {exc}") from exc

    inv = _extraer_invoice(raiz)

    numero = _texto(inv, "cbc:ID")
    if not numero:
        raise ParserUBLError("Falta cbc:ID (número de factura).")

    tipo_documento = _texto(inv, "cbc:InvoiceTypeCode", "01")
    moneda = _texto(inv, "cbc:DocumentCurrencyCode", "COP")
    fecha = _fecha(inv, "cbc:IssueDate")

    emisor = _tercero(inv.find("cac:AccountingSupplierParty/cac:Party", NS))
    adquirente = _tercero(inv.find("cac:AccountingCustomerParty/cac:Party", NS))

    # Condición de pago: 1 = contado, 2 = crédito (DIAN)
    payment_means_id = _texto(inv, "cac:PaymentMeans/cbc:ID", "1")
    condicion = CondicionPago.CREDITO if payment_means_id == "2" else CondicionPago.CONTADO
    medio_pago = _texto(inv, "cac:PaymentMeans/cbc:PaymentMeansCode") or None

    # Líneas
    lineas: list[LineaFactura] = []
    for idx, linea_nodo in enumerate(inv.findall("cac:InvoiceLine", NS), start=1):
        lid = _texto(linea_nodo, "cbc:ID") or str(idx)
        desc = _texto(linea_nodo, "cac:Item/cbc:Description") or _texto(
            linea_nodo, "cac:Item/cbc:Name"
        )
        cantidad = _decimal(linea_nodo, "cbc:InvoicedQuantity", "1")
        valor = _decimal(linea_nodo, "cbc:LineExtensionAmount")
        lineas.append(
            LineaFactura(
                id=lid,
                descripcion=desc,
                cantidad=cantidad,
                valor_linea=valor,
            )
        )

    # Impuestos
    impuestos: list[Impuesto] = []
    for tax_total in inv.findall("cac:TaxTotal", NS):
        for sub in tax_total.findall("cac:TaxSubtotal", NS):
            codigo = _texto(sub, "cac:TaxCategory/cac:TaxScheme/cbc:ID")
            valor = _decimal(sub, "cbc:TaxAmount")
            porcentaje_raw = _texto(sub, "cac:TaxCategory/cbc:Percent")
            porcentaje = Decimal(porcentaje_raw) if porcentaje_raw else None
            if codigo:
                impuestos.append(Impuesto(codigo=codigo, valor=valor, porcentaje=porcentaje))

    monetary = inv.find("cac:LegalMonetaryTotal", NS)
    subtotal = _decimal(monetary, "cbc:LineExtensionAmount")
    descuento = _decimal(monetary, "cbc:AllowanceTotalAmount")
    cargos = _decimal(monetary, "cbc:ChargeTotalAmount")
    base = _decimal(monetary, "cbc:TaxExclusiveAmount")
    total_con_imp = _decimal(monetary, "cbc:TaxInclusiveAmount")
    total_pagar = _decimal(monetary, "cbc:PayableAmount")

    return Factura(
        numero=numero,
        fecha=fecha,
        tipo_documento=tipo_documento,
        moneda=moneda,
        emisor=emisor,
        adquirente=adquirente,
        condicion_pago=condicion,
        medio_pago=medio_pago,
        lineas=tuple(lineas),
        impuestos=tuple(impuestos),
        subtotal_lineas=subtotal,
        descuento_global=descuento,
        cargos_globales=cargos,
        base_gravable=base,
        total_con_impuestos=total_con_imp,
        total_a_pagar=total_pagar,
    )