class ErrorDeDominio(Exception):
    """Base de los errores de reglas de negocio."""


class AsientoInvalidoError(ErrorDeDominio):
    """El asiento no cumple la partida doble o tiene partidas inválidas."""


class CuentaNoExisteError(ErrorDeDominio):
    """La cuenta no existe en el catálogo del PUC cargado."""


class FacturaDuplicadaError(ErrorDeDominio):
    """Ya existe una transacción registrada con ese número de factura."""


class TransicionInvalidaError(ErrorDeDominio):
    """El cambio de estado pedido no está permitido desde el estado actual."""


class TransaccionNoEncontradaError(ErrorDeDominio):
    """No existe una transacción con el id indicado."""
