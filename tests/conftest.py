import pytest  # type: ignore[reportMissingImports]

from agente.config import get_settings
from agente.domain.puc import CatalogoPUC


@pytest.fixture
def catalogo() -> CatalogoPUC:
    return CatalogoPUC.desde_csv(get_settings().directorio_puc)
