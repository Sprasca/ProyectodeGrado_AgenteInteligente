from enum import StrEnum


class Naturaleza(StrEnum):
    DEBITO = "DEBITO"
    CREDITO = "CREDITO"


class CondicionPago(StrEnum):
    """Código DIAN de PaymentMeans/ID: 1 = contado, 2 = crédito."""

    CONTADO = "CONTADO"
    CREDITO = "CREDITO"


class OrigenRegla(StrEnum):
    """Quién creó la regla: la siembra desde data/puc o el contador desde la API."""

    CSV = "csv"
    CONTADOR = "contador"


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
    RECHAZADA = "RECHAZADA"  # estado final: el contador descartó la transacción


class Rol(StrEnum):
    """Rol de un usuario; sus permisos se definen en agente.api.permisos."""

    CONTADOR = "CONTADOR"
    AUXILIAR = "AUXILIAR"
    ADMIN = "ADMIN"


class TipoEvento(StrEnum):
    """Hechos que quedan en evento_transaccion."""

    RECIBIDA = "RECIBIDA"
    CLASIFICADA = "CLASIFICADA"
    APROBADA = "APROBADA"
    CORREGIDA = "CORREGIDA"
    RECHAZADA = "RECHAZADA"
    CONSOLIDADA = "CONSOLIDADA"  # incluida en un reporte diario (RF06)
    LIBERADA = "LIBERADA"  # su reporte se anuló: vuelve a poder corregirse y consolidarse
    EXPORTADA = "EXPORTADA"  # su reporte se exportó al ERP (RF07), con el usuario que exportó


class EstadoReporte(StrEnum):
    """Estados del reporte de transacciones diarias (RF06, RF07). ANULADO es una ampliación del manual."""

    CONSOLIDADO = "CONSOLIDADO"
    EXPORTADO = "EXPORTADO"  # final: nunca se anula ni se exporta otra vez
    ANULADO = "ANULADO"  # final: sus transacciones quedan libres


class TipoReporte(StrEnum):
    """Hasta 4 PARCIALES vigentes por jornada, hechos a mano, y un solo CIERRE con corte a la HORA_CIERRE."""

    PARCIAL = "PARCIAL"
    CIERRE = "CIERRE"


class TipoAlertaTecnica(StrEnum):
    JORNADA_SIN_CIERRE = "JORNADA_SIN_CIERRE"


class TipoDocumento(StrEnum):
    XML = "XML"
    PDF = "PDF"


class CanalRecepcion(StrEnum):
    """Por dónde llegó una factura: carga manual por la API o recepción automática en la carpeta (RF01)."""

    API = "api"
    CARPETA = "carpeta"
