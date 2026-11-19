# Agente inteligente para automatizar el registro de ventas bajo el PUC

Proyecto de grado, Ingeniería de Sistemas, Universidad de Cartagena.

El agente recibe la factura electrónica de venta (XML UBL de la DIAN), la interpreta, propone el asiento contable bajo el PUC del Grupo 2 y deja en alerta lo que no clasifica con suficiente confianza.

## Estado: Sprint 2 En Pruebas

| Componente | Estado |
|---|---|
| Dominio: partida doble, catálogo PUC, motor de reglas | Listo, con pruebas |
| Parser UBL 2.1 (Invoice y AttachedDocument básico) | Listo, con pruebas |
| PostgreSQL + pgvector, migración inicial y carga del catálogo | Listo |
| Caso de uso `clasificar_hibrido` (application) | Listo |
| API HTTP (FastAPI / Groq u otro) | Listo |
| NLP + RAG con pgvector | Sprint 2, NLP en proceso de prueba|
| Alertas, reporte diario, exportación al ERP, auditoría | Sprint 3 |

> El catálogo de `data/puc/` es una **semilla** de cuentas de ingresos por ventas. Las facturas de `tests/fixtures/` son sintéticas, no documentos reales. La carpeta de facturas almacena documentos sintéticos y reales que se usan como ejemplo para las pruebas.  

## Requisitos
- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker (para PostgreSQL con pgvector)

## Puesta en marcha
```bash
cp .env.example .env
uv sync
docker compose up -d db
uv run alembic upgrade head          # crea el esquema
uv run python  # carga cuentas y reglas (idempotente)
