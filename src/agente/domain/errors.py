class ErrorDeDominio(Exception):
    """Base de los errores de reglas de negocio."""


class AsientoInvalidoError(ErrorDeDominio):
    """El asiento no cumple la partida doble o tiene partidas inválidas."""


class CuentaNoExisteError(ErrorDeDominio):
    """La cuenta no existe en el catálogo del PUC cargado."""


class CatalogoInvalidoError(ErrorDeDominio):
    """El catálogo de data/puc es inconsistente (códigos repetidos, reglas mal dirigidas)."""


class FacturaDuplicadaError(ErrorDeDominio):
    """Ya existe una transacción registrada con ese número de factura."""


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
