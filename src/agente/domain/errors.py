class ErrorDeDominio(Exception):
    """Base de los errores de reglas de negocio."""


class AsientoInvalidoError(ErrorDeDominio):
    """El asiento no cumple la partida doble o tiene partidas inválidas."""


class CuentaNoExisteError(ErrorDeDominio):
    """La cuenta no existe en el catálogo del PUC cargado."""


class NoEsVentaError(ErrorDeDominio):
    """El documento no es una venta; el agente no lo procesa (RN01)."""
