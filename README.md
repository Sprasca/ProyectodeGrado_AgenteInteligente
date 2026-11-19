# Agente inteligente para automatizar el registro de ventas bajo el PUC

Proyecto de grado, Ingeniería de Sistemas, Universidad de Cartagena.

El agente recibe la factura electrónica de venta (XML UBL de la DIAN), la interpreta, propone el asiento contable bajo el PUC del Grupo 2 y deja en alerta lo que no clasifica con suficiente confianza.

## Estado: Sprint 2 En Pruebas

| Componente | Estado |
|---|---|
| Dominio: partida doble, catálogo PUC, motor de reglas | Listo, con pruebas |
| Emparejamiento por palabra completa, puntaje y detección de líneas ambiguas | Listo, con pruebas |
| Parser UBL 2.1 (Invoice y AttachedDocument) | Listo, con pruebas |
| Lector de PDF con texto embebido (heurísticas locales) | Listo, con pruebas |
| PostgreSQL + pgvector, migraciones y carga del catálogo | Listo |
| Persistencia de transacciones (guardar, listar, aprobar, corregir) | Listo, con pruebas unitarias y de integración |
| Caso de uso `clasificar_hibrido` (reglas + Groq para líneas sin regla) | Listo, con pruebas |
| API HTTP (FastAPI) | Listo, con pruebas |
| NLP + RAG con pgvector | Sprint 2, NLP en proceso de prueba |
| Reporte diario, exportación al ERP, auditoría | Sprint 3 |

> El catálogo de `data/puc/` es una **semilla** de cuentas de ingresos por ventas. Las facturas de `tests/fixtures/` son sintéticas, no documentos reales. La carpeta de facturas almacena documentos sintéticos y reales que se usan como ejemplo para las pruebas.

## Requisitos
- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker (para PostgreSQL con pgvector)

## Puesta en marcha
```bash
cp .env.example .env
uv sync
docker compose up -d db
uv run alembic upgrade head              # crea el esquema (migraciones 0001 y 0002)
uv run python scripts/sembrar_puc.py     # carga cuentas y reglas (idempotente)
```

## API
```bash
uv run uvicorn agente.api.main:app --reload
```
Documentación interactiva en http://127.0.0.1:8000/docs.

| Método y ruta | Descripción |
|---|---|
| `GET /salud` | Comprobación de vida |
| `POST /facturas/clasificar?usar_nlp=true&guardar=true` | Clasifica un `.xml` o `.pdf`. Con `guardar=true` lo registra y devuelve su `id` (409 si la factura ya existe, 503 si la base no responde) |
| `POST /carpeta/procesar` | Clasifica todos los archivos de una carpeta (no guarda) |
| `GET /transacciones?estado=EN_ALERTA` | Lista transacciones, con filtro opcional por estado |
| `GET /transacciones/{id}` | Transacción con sus partidas y el detalle por línea |
| `POST /transacciones/{id}/aprobar` | El contador aprueba una transacción `EN_ALERTA` con asiento propuesto |
| `POST /transacciones/{id}/corregir` | El contador reemplaza las partidas: `{"partidas": [{"cuenta": "1105", "naturaleza": "DEBITO", "valor": 119000}, ...]}` |

## Pruebas
```bash
uv run pytest                    # unitarias: no requieren red, Groq ni PostgreSQL
uv run pytest -m integracion     # integración contra PostgreSQL real (DATABASE_URL)
```
Las pruebas de integración necesitan la base levantada, migrada y sembrada (ver *Puesta en marcha*). Si no hay conexión se saltan con un aviso.

## Demo web
`uv run python scripts/empaquetar_demo.py` regenera `docs/agente_demo.zip`, que `docs/index.html` ejecuta en el navegador con Pyodide (solo el motor de reglas).
