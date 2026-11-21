import os

import pytest  # type: ignore[reportMissingImports]

from agente.config import get_settings
from agente.domain.puc import CatalogoPUC

# Las pruebas unitarias clasifican con los CSV de data/puc: no requieren PostgreSQL.
# La prueba de integración de punta a punta cambia a "bd" de forma explícita.
os.environ["FUENTE_CATALOGO"] = "csv"
get_settings.cache_clear()


@pytest.fixture
def catalogo() -> CatalogoPUC:
    return CatalogoPUC.desde_csv(get_settings().directorio_puc)
