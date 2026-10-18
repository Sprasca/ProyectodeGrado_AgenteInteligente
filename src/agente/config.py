from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://agente:agente@localhost:5432/agente"
    umbral_confianza: Decimal = Decimal("0.80")
    directorio_puc: Path = RAIZ / "data" / "puc"


@lru_cache
def get_settings() -> Settings:
    return Settings()