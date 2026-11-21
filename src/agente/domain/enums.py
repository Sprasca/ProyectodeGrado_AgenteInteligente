from enum import StrEnum


class Naturaleza(StrEnum):
    DEBITO = "DEBITO"
    CREDITO = "CREDITO"


class CondicionPago(StrEnum):
    """Código DIAN de PaymentMeans/ID: 1 = contado, 2 = crédito."""

    CONTADO = "CONTADO"
    CREDITO = "CREDITO"


class AccionHistorial(StrEnum):
    """Cambios de una regla que quedan en historial_regla."""

    CREAR = "CREAR"
    EDITAR = "EDITAR"
    ACTIVAR = "ACTIVAR"
    DESACTIVAR = "DESACTIVAR"


class EstadoLinea(StrEnum):
    """Resultado del emparejamiento de una línea de la factura contra las reglas."""

    CLASIFICADA = "CLASIFICADA"
    SIN_REGLA = "SIN_REGLA"
    AMBIGUA = "AMBIGUA"


class EstadoTransaccion(StrEnum):
    APROBADA_AUTOMATICA = "APROBADA_AUTOMATICA"
    EN_ALERTA = "EN_ALERTA"
    APROBADA_CONTADOR = "APROBADA_CONTADOR"
    CORREGIDA_CONTADOR = "CORREGIDA_CONTADOR"
