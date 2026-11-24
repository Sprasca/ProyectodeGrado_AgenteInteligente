class ErrorDeDominio(Exception):
    """Base de los errores de reglas de negocio."""


class AsientoInvalidoError(ErrorDeDominio):
    """El asiento no cumple la partida doble o tiene partidas inválidas."""


class CuentaNoExisteError(ErrorDeDominio):
    """La cuenta no existe en el catálogo del PUC cargado."""


class CatalogoInvalidoError(ErrorDeDominio):
    """El catálogo de data/puc es inconsistente (códigos repetidos, reglas mal dirigidas)."""


class FacturaDuplicadaError(ErrorDeDominio):
    """
    Ya existe una transacción con ese número de factura, CUFE o documento (SHA-256).
    id_existente es la transacción original, para que el contador la ubique (None si no se pudo saber).
    """

    def __init__(self, mensaje: str, id_existente: object | None = None) -> None:
        super().__init__(mensaje)
        self.id_existente = id_existente


class TransicionInvalidaError(ErrorDeDominio):
    """El cambio de estado pedido no está permitido desde el estado actual."""


class TransaccionNoEncontradaError(ErrorDeDominio):
    """No existe una transacción con el id indicado."""


class DatosInvalidosError(ErrorDeDominio):
    """Los datos enviados tienen errores que impiden guardarlos."""

    def __init__(self, errores: tuple[str, ...] | list[str]) -> None:
        self.errores = tuple(errores)
        super().__init__(" ".join(self.errores))


class ReglaInvalidaError(DatosInvalidosError):
    """La regla tiene errores de validación (RF09)."""


class CuentaInvalidaError(DatosInvalidosError):
    """La cuenta nueva tiene errores de validación."""


class ConflictoReglaError(ErrorDeDominio):
    """La regla es válida pero sus palabras clave chocan con otras reglas activas; falta confirmar."""

    def __init__(self, advertencias: tuple) -> None:
        self.advertencias = tuple(advertencias)
        super().__init__(
            "La regla tiene palabras clave que ya usan otras reglas activas con otra cuenta. "
            "Revise las advertencias y repita la solicitud con confirmar=true para guardarla."
        )


class CuentaDuplicadaError(ErrorDeDominio):
    """Ya existe una cuenta con ese código en el catálogo."""


class ReglaNoEncontradaError(ErrorDeDominio):
    """No existe una regla con ese código."""


class UsuarioInvalidoError(DatosInvalidosError):
    """Los datos del usuario tienen errores de validación."""


class UsuarioDuplicadoError(ErrorDeDominio):
    """Ya existe un usuario con ese nombre de usuario."""


class UsuarioNoEncontradoError(ErrorDeDominio):
    """No existe un usuario con ese id."""


class UltimoAdministradorError(ErrorDeDominio):
    """El cambio dejaría el sistema sin ningún ADMIN activo."""


class MotivoInvalidoError(DatosInvalidosError):
    """Falta el motivo de una acción del contador o es demasiado corto."""


class RutaNoPermitidaError(ErrorDeDominio):
    """La carpeta pedida está fuera de DIRECTORIO_ENTRADA."""


class DocumentoInvalidoError(ErrorDeDominio):
    """El archivo no es un XML o PDF de factura completo y legible (no se reintenta)."""


# --- Reportes diarios (RF06, RF07) ----------------------------------------------------------------------


class ConsolidacionBloqueadaError(ErrorDeDominio):
    """Hay transacciones EN_ALERTA sin resolver hasta el corte (RF06 1.a): no se consolida."""

    def __init__(self, pendientes: int) -> None:
        super().__init__(
            f"No se puede consolidar: hay {pendientes} transacción(es) EN_ALERTA sin resolver hasta el corte. "
            "El contador debe resolverlas (aprobar, corregir o rechazar) antes de consolidar."
        )
        self.pendientes = pendientes


class SinTransaccionesError(ErrorDeDominio):
    """No hay transacciones aprobadas sin reporte hasta el corte: no se crea un reporte vacío."""


class ConsolidacionInconsistenteError(ErrorDeDominio):
    """Error de integridad del sistema (RF06 3.a): débitos y créditos no cuadran o falta un asiento."""


class ReporteNoEncontradoError(ErrorDeDominio):
    """No existe un reporte con el id indicado."""


class ExportacionFallidaError(ErrorDeDominio):
    """No se pudo generar el archivo de exportación (RF07 anomalía 4): el reporte sigue CONSOLIDADO."""

