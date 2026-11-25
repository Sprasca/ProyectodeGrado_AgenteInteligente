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


class ClaseNotificacion(StrEnum):
    """CONTABLE: para el contador y el auxiliar. TECNICA: para el responsable de mantenimiento (ADMIN)."""

    CONTABLE = "CONTABLE"
    TECNICA = "TECNICA"


class TipoNotificacion(StrEnum):
    """Causas de una notificación (RF04 y las alertas que piden RF01, RF06 y RF07)."""

    # Contables
    TRANSACCION_EN_ALERTA = "TRANSACCION_EN_ALERTA"  # RF04 y RN05
    CONSOLIDACION_BLOQUEADA = "CONSOLIDACION_BLOQUEADA"  # RF06 1.a
    REPORTE_DISPONIBLE = "REPORTE_DISPONIBLE"  # RF06 paso 8
    FACTURA_DUPLICADA = "FACTURA_DUPLICADA"  # documento enviado dos veces a la carpeta
    # Técnicas
    DESCUADRE = "DESCUADRE"  # RF06 3.a
    EXPORTACION_FALLIDA = "EXPORTACION_FALLIDA"  # RF07 anomalía 4
    JORNADA_SIN_CIERRE = "JORNADA_SIN_CIERRE"  # cierre programado sin reporte de cierre
    ARCHIVO_NO_RECIBIDO = "ARCHIVO_NO_RECIBIDO"  # RF01 1.a y 2.a
    DIRECTORIO_NO_DISPONIBLE = "DIRECTORIO_NO_DISPONIBLE"  # RF01 3.a
    ERROR_RECEPCION = "ERROR_RECEPCION"  # error inesperado al recibir un archivo
    CORREO_NO_ENVIADO = "CORREO_NO_ENVIADO"  # RF04 anomalía 2


class EstadoEnvio(StrEnum):
    """Envío de una notificación por correo (canal opcional)."""

    PENDIENTE = "PENDIENTE"
    ENVIADA = "ENVIADA"
    FALLIDA = "FALLIDA"  # agotó los intentos; la notificación sigue en la bandeja


class TipoDocumento(StrEnum):
    XML = "XML"
    PDF = "PDF"


class CanalRecepcion(StrEnum):
    """Por dónde llegó una factura: carga manual por la API o recepción automática en la carpeta (RF01)."""

    API = "api"
    CARPETA = "carpeta"
