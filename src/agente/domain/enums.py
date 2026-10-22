from enum import StrEnum


class Naturaleza(StrEnum):
    DEBITO = "DEBITO"
    CREDITO = "CREDITO"


class CondicionPago(StrEnum):
    """Código DIAN de PaymentMeans/ID: 1 = contado, 2 = crédito."""

    CONTADO = "CONTADO"
    CREDITO = "CREDITO"


class EstadoTransaccion(StrEnum):
    APROBADA_AUTOMATICA = "APROBADA_AUTOMATICA"
    EN_ALERTA = "EN_ALERTA"
    APROBADA_CONTADOR = "APROBADA_CONTADOR"
    CORREGIDA_CONTADOR = "CORREGIDA_CONTADOR"
