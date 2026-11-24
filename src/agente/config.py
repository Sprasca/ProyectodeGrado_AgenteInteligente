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
        # Firma de los tokens JWT. Sin él la API no arranca (ver agente.api.autenticacion).
        self.jwt_secreto = obtener("JWT_SECRETO", "").strip()
        minutos = obtener("JWT_MINUTOS", "60").strip()
        if not minutos.isdigit() or int(minutos) <= 0:
            raise ValueError(f"JWT_MINUTOS debe ser un número entero positivo, no {minutos!r}.")
        self.jwt_minutos = int(minutos)
        # Orígenes (navegador) que pueden llamar la API, separados por comas. Vacío: ninguno.
        self.cors_origenes = [o.strip().rstrip("/") for o in obtener("CORS_ORIGENES", "").split(",") if o.strip()]
        if "*" in self.cors_origenes:
            raise ValueError("CORS_ORIGENES no acepta '*': liste los orígenes permitidos, separados por comas.")
        # /carpeta/procesar solo lee carpetas dentro de este directorio.
        self.directorio_entrada = Path(obtener("DIRECTORIO_ENTRADA", str(self.directorio_facturas)))
        # Tamaño máximo de un documento de factura (carga por la API y recepción automática).
        tamano = obtener("TAMANO_MAXIMO_DOCUMENTO_MB", "10").strip()
        if not tamano.isdigit() or int(tamano) <= 0:
            raise ValueError(f"TAMANO_MAXIMO_DOCUMENTO_MB debe ser un número entero positivo, no {tamano!r}.")
        self.tamano_maximo_documento_mb = int(tamano)

    @property
    def tamano_maximo_documento(self) -> int:
        """En bytes."""
        return self.tamano_maximo_documento_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
