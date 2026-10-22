"""Carga el catálogo semilla (data/puc/*.csv) en PostgreSQL. Es idempotente.

Uso: uv run python scripts/sembrar_puc.py
"""

from agente.config import get_settings
from agente.domain.puc import CatalogoPUC
from agente.infrastructure.db.repositorio import sembrar_catalogo
from agente.infrastructure.db.session import crear_sessionmaker


def main() -> None:
    catalogo = CatalogoPUC.desde_csv(get_settings().directorio_puc)
    with crear_sessionmaker()() as sesion, sesion.begin():
        sembrar_catalogo(sesion, catalogo)
    print(f"Catálogo cargado: {len(catalogo.cuentas)} cuentas, {len(catalogo.reglas)} reglas.")


if __name__ == "__main__":
    main()