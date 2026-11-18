from decimal import Decimal

from .asiento import AsientoContable, Partida
from .enums import CondicionPago, EstadoTransaccion, Naturaleza
from .errors import AsientoInvalidoError, CuentaNoExisteError
from .factura import CODIGO_IVA, MEDIO_PAGO_EFECTIVO, Factura
from .puc import CatalogoPUC, ReglaIngreso, normalizar
from .resultado import ResultadoClasificacion


def _encontrar_regla(descripcion: str, reglas: tuple[ReglaIngreso, ...]) -> ReglaIngreso | None:
    """Devuelve la primera regla cuyas palabras clave aparecen en la descripción normalizada."""
    texto = normalizar(descripcion)
    for regla in reglas:
        if any(palabra in texto for palabra in regla.palabras_clave):
            return regla
    return None


def clasificar(
    factura: Factura,
    catalogo: CatalogoPUC,
    umbral_confianza: Decimal = Decimal("0.80"),
) -> ResultadoClasificacion:
    """
    Aplica las reglas del catálogo (data/puc) y construye el asiento de partida doble.

    Las cuentas de caja, bancos, clientes e IVA se resuelven desde el catálogo actual,
    de modo que un cambio del contador en los CSV no exige tocar este módulo.
    """
    motivos: list[str] = []
    reglas_aplicadas: list[str] = []

    if not factura.es_venta:
        motivos.append(f"Tipo de documento {factura.tipo_documento!r} no es de venta (RN01).")
        return ResultadoClasificacion(
            factura=factura,
            asiento=None,
            estado=EstadoTransaccion.EN_ALERTA,
            confianza=Decimal("0"),
            reglas_aplicadas=(),
            motivos_alerta=tuple(motivos),
        )

    if not factura.lineas:
        motivos.append("La factura no tiene líneas de detalle.")
        return ResultadoClasificacion(
            factura=factura,
            asiento=None,
            estado=EstadoTransaccion.EN_ALERTA,
            confianza=Decimal("0"),
            reglas_aplicadas=(),
            motivos_alerta=tuple(motivos),
        )

    ingresos_por_cuenta: dict[str, Decimal] = {}
    lineas_emparejadas = 0

    for linea in factura.lineas:
        regla = _encontrar_regla(linea.descripcion, catalogo.reglas)
        if regla is None:
            motivos.append(f"Línea {linea.id}: ninguna regla coincide con «{linea.descripcion}».")
            continue
        lineas_emparejadas += 1
        if regla.codigo not in reglas_aplicadas:
            reglas_aplicadas.append(regla.codigo)
        ingresos_por_cuenta[regla.cuenta_ingreso] = (
            ingresos_por_cuenta.get(regla.cuenta_ingreso, Decimal("0")) + linea.valor_linea
        )

    if not ingresos_por_cuenta:
        motivos.append("Ninguna línea pudo clasificarse con las reglas actuales.")
        return ResultadoClasificacion(
            factura=factura,
            asiento=None,
            estado=EstadoTransaccion.EN_ALERTA,
            confianza=Decimal("0"),
            reglas_aplicadas=(),
            motivos_alerta=tuple(motivos),
        )

    confianza = (Decimal(lineas_emparejadas) / Decimal(len(factura.lineas))).quantize(Decimal("0.001"))

    valor_iva = Decimal("0")
    for imp in factura.impuestos:
        if imp.codigo == CODIGO_IVA:
            valor_iva += imp.valor

    # Cuenta de ingreso dominante (la de mayor valor) orienta clientes bienes vs servicios
    cuenta_ingreso_ref = max(ingresos_por_cuenta, key=ingresos_por_cuenta.get)  # type: ignore[arg-type]

    try:
        cuenta_debito = catalogo.cuenta_debito_venta(
            a_credito=factura.condicion_pago is CondicionPago.CREDITO,
            efectivo=factura.medio_pago == MEDIO_PAGO_EFECTIVO,
            cuenta_ingreso=cuenta_ingreso_ref,
        )
        cuenta_iva = catalogo.cuenta_iva() if valor_iva > 0 else None
    except CuentaNoExisteError as exc:
        motivos.append(str(exc))
        return ResultadoClasificacion(
            factura=factura,
            asiento=None,
            estado=EstadoTransaccion.EN_ALERTA,
            confianza=confianza,
            reglas_aplicadas=tuple(reglas_aplicadas),
            motivos_alerta=tuple(motivos),
        )

    partidas: list[Partida] = [
        Partida(
            cuenta=cuenta_debito,
            naturaleza=Naturaleza.DEBITO,
            valor=factura.total_a_pagar,
            descripcion=f"Cobro factura {factura.numero}",
        )
    ]

    for codigo_cuenta, valor in ingresos_por_cuenta.items():
        partidas.append(
            Partida(
                cuenta=codigo_cuenta,
                naturaleza=Naturaleza.CREDITO,
                valor=valor,
                descripcion="Ingreso por venta",
            )
        )

    if cuenta_iva is not None and valor_iva > 0:
        partidas.append(
            Partida(
                cuenta=cuenta_iva,
                naturaleza=Naturaleza.CREDITO,
                valor=valor_iva,
                descripcion="IVA generado",
            )
        )

    try:
        for p in partidas:
            catalogo.obtener(p.cuenta)
        asiento = AsientoContable(tuple(partidas))
    except (CuentaNoExisteError, AsientoInvalidoError) as exc:
        motivos.append(str(exc))
        return ResultadoClasificacion(
            factura=factura,
            asiento=None,
            estado=EstadoTransaccion.EN_ALERTA,
            confianza=confianza,
            reglas_aplicadas=tuple(reglas_aplicadas),
            motivos_alerta=tuple(motivos),
        )

    if confianza < umbral_confianza:
        motivos.append(f"Confianza {confianza} por debajo del umbral {umbral_confianza}.")

    estado = (
        EstadoTransaccion.APROBADA_AUTOMATICA
        if not motivos and confianza >= umbral_confianza
        else EstadoTransaccion.EN_ALERTA
    )

    return ResultadoClasificacion(
        factura=factura,
        asiento=asiento,
        estado=estado,
        confianza=confianza,
        reglas_aplicadas=tuple(reglas_aplicadas),
        motivos_alerta=tuple(motivos),
    )
