import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# De dónde sale el catálogo con el que se clasifica: la base de datos (lo que administra
# el contador) o los CSV de data/puc (pruebas unitarias y demo sin base de datos).
FUENTES_CATALOGO = ("bd", "csv")


class Settings:
    def __init__(self) -> None:
        valores_env: dict[str, str] = {}
        archivo_env = Path(".env")
        if archivo_env.is_file():
            for linea in archivo_env.read_text(encoding="utf-8").splitlines():
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    clave, valor = linea.split("=", 1)
                    valores_env[clave.strip().upper()] = valor.strip().strip("\"'")

        def obtener(nombre: str, predeterminado: str) -> str:
            return os.environ.get(nombre, valores_env.get(nombre, predeterminado))

        self.database_url = obtener("DATABASE_URL", "postgresql+psycopg://agente:agente@localhost:5432/agente")
        self.umbral_confianza = Decimal(obtener("UMBRAL_CONFIANZA", "0.80"))
        self.directorio_puc = Path(obtener("DIRECTORIO_PUC", str(RAIZ / "data" / "puc")))
        self.groq_api_key = obtener("GROQ_API_KEY", "")
        self.groq_modelo = obtener("GROQ_MODELO", "llama-3.3-70b-versatile")
        self.directorio_facturas = Path(obtener("DIRECTORIO_FACTURAS", str(RAIZ / "facturas")))
        self.fuente_catalogo = obtener("FUENTE_CATALOGO", "bd").strip().lower()
        if self.fuente_catalogo not in FUENTES_CATALOGO:
            raise ValueError(f"FUENTE_CATALOGO debe ser 'bd' o 'csv', no {self.fuente_catalogo!r}.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
