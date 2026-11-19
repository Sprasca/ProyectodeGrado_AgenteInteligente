from decimal import Decimal

from .asiento import AsientoContable, Partida
from .enums import CondicionPago, EstadoLinea, EstadoTransaccion, Naturaleza
from .errors import AsientoInvalidoError, CuentaNoExisteError
from .factura import CODIGO_IVA, MEDIO_PAGO_EFECTIVO, Factura, LineaFactura
from .puc import CatalogoPUC, ReglaIngreso, palabras
from .resultado import ClasificacionLinea, ResultadoClasificacion


def emparejar_linea(linea: LineaFactura, reglas: tuple[ReglaIngreso, ...]) -> ClasificacionLinea:
    """
    Evalúa TODAS las reglas contra la descripción de la línea.

    Puntaje de una regla = número de palabras clave distintas que coinciden como palabra
    completa (o plural simple). Gana el puntaje máximo; si en el máximo empatan cuentas
    distintas, la línea es AMBIGUA y no se clasifica.
    """
    tokens = palabras(linea.descripcion)
    con_puntaje = [(r, encontradas) for r in reglas if (encontradas := r.palabras_coincidentes(tokens))]

    if not con_puntaje:
        return ClasificacionLinea(
            id_linea=linea.id,
            descripcion=linea.descripcion,
            estado=EstadoLinea.SIN_REGLA,
            cuenta_ingreso=None,
            regla=None,
            palabras_encontradas=(),
            cuentas_candidatas=(),
        )

    maximo = max(len(encontradas) for _, encontradas in con_puntaje)
    ganadoras = [(r, encontradas) for r, encontradas in con_puntaje if len(encontradas) == maximo]
    cuentas = tuple(dict.fromkeys(r.cuenta_ingreso for r, _ in ganadoras))
    palabras_encontradas = tuple(dict.fromkeys(p for _, encontradas in ganadoras for p in encontradas))

    if len(cuentas) > 1:
        return ClasificacionLinea(
            id_linea=linea.id,
            descripcion=linea.descripcion,
            estado=EstadoLinea.AMBIGUA,
            cuenta_ingreso=None,
            regla=None,
            palabras_encontradas=palabras_encontradas,
            cuentas_candidatas=cuentas,
        )

    regla = ganadoras[0][0]
    return ClasificacionLinea(
        id_linea=linea.id,
        descripcion=linea.descripcion,
        estado=EstadoLinea.CLASIFICADA,
        cuenta_ingreso=regla.cuenta_ingreso,
        regla=regla.codigo,
        palabras_encontradas=palabras_encontradas,
        cuentas_candidatas=cuentas,
    )


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
    lineas: list[ClasificacionLinea] = []

    def en_alerta(confianza: Decimal, reglas: tuple[str, ...] = ()) -> ResultadoClasificacion:
        return ResultadoClasificacion(
            factura=factura,
            asiento=None,
            estado=EstadoTransaccion.EN_ALERTA,
            confianza=confianza,
            reglas_aplicadas=reglas,
            motivos_alerta=tuple(motivos),
            lineas=tuple(lineas),
        )

    if not factura.es_venta:
        motivos.append(f"Tipo de documento {factura.tipo_documento!r} no es de venta (RN01).")
        return en_alerta(Decimal("0"))

    if not factura.lineas:
        motivos.append("La factura no tiene líneas de detalle.")
        return en_alerta(Decimal("0"))

    ingresos_por_cuenta: dict[str, Decimal] = {}
    lineas_emparejadas = 0

    for linea in factura.lineas:
        clasificacion = emparejar_linea(linea, catalogo.reglas)
        lineas.append(clasificacion)
        if clasificacion.estado is EstadoLinea.SIN_REGLA:
            motivos.append(f"Línea {linea.id}: ninguna regla coincide con «{linea.descripcion}».")
            continue
        if clasificacion.estado is EstadoLinea.AMBIGUA:
            motivos.append(
                f"Línea {linea.id}: «{linea.descripcion}» es ambigua entre las cuentas "
                f"{', '.join(clasificacion.cuentas_candidatas)} "
                f"(palabras: {', '.join(clasificacion.palabras_encontradas)})."
            )
            continue
        cuenta = clasificacion.cuenta_ingreso
        assert cuenta is not None and clasificacion.regla is not None
        lineas_emparejadas += 1
        if clasificacion.regla not in reglas_aplicadas:
            reglas_aplicadas.append(clasificacion.regla)
        ingresos_por_cuenta[cuenta] = ingresos_por_cuenta.get(cuenta, Decimal("0")) + linea.valor_linea

    if not ingresos_por_cuenta:
        motivos.append("Ninguna línea pudo clasificarse con las reglas actuales.")
        return en_alerta(Decimal("0"))

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
        return en_alerta(confianza, tuple(reglas_aplicadas))

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
        return en_alerta(confianza, tuple(reglas_aplicadas))

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
        lineas=tuple(lineas),
    )
