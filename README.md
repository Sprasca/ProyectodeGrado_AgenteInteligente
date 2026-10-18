# Agente inteligente para automatizar el registro de ventas bajo el PUC

Proyecto de grado, Ingeniería de Sistemas, Universidad de Cartagena. 

El agente recibe la factura electrónica de venta (XML UBL de la DIAN), la interpreta, propone el asiento contable
bajo el PUC del Grupo 2 y envía a revisión del contador lo que no clasifica con suficiente confianza.

## Estado: Sprint 1 (reglas de clasificación y base)

| Componente | Estado |
|---|---|
| Dominio: partida doble, catálogo PUC, motor de reglas | Listo, con pruebas |
| Parser UBL 2.1 y `AttachedDocument` (RF01/RF02, parte determinista) | Listo, con pruebas |
| PostgreSQL + pgvector, migración inicial y carga del catálogo | Listo, probado en PostgreSQL 16 |
| API FastAPI: `/facturas/parsear`, `/facturas/clasificar` | Listo |
| NLP + RAG con pgvector | Sprint 2 |
| Alertas, reporte diario, exportación al ERP, auditoría | Sprint 3 |

> El catálogo de `data/puc/` es una **semilla** de cuentas de ingresos por ventas. Debe validarse con el experto
> contable antes de usarla como regla de negocio. Las facturas de `tests/fixtures/`
> son sintéticas, no documentos reales.

## Requisitos
- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker (para PostgreSQL con pgvector y Mailpit)

## Puesta en marcha
```bash
cp .env.example .env
uv sync
docker compose up -d db
uv run alembic upgrade head          # crea el esquema
uv run python scripts/sembrar_puc.py # carga cuentas y reglas (idempotente)
uv run uvicorn agente.api.main:app --reload
```
Abrir http://localhost:8000/docs y subir un XML de `tests/fixtures/` en `POST /facturas/clasificar`.

## Pruebas
```bash
uv run pytest                    # unitarias, no necesitan base de datos
uv run pytest -m integration     # requieren docker compose up -d db + migraciones + semilla
uv run ruff check . && uv run ruff format --check .
```

## Arquitectura (Clean Architecture)
```
src/agente/
  domain/          reglas puras, sin FastAPI ni BD: Factura, AsientoContable, CatalogoPUC, MotorReglas
  application/     casos de uso (clasificar_factura)
  infrastructure/  db (SQLAlchemy/Alembic), dian (parser UBL), nlp (Sprint 2), notifications (Sprint 3)
  api/             FastAPI y esquemas de salida
```
Decisión central: el LLM (Sprint 2) solo propondrá cuenta y confianza. El dominio valida siempre la partida doble
y la existencia de las cuentas; si algo falla, la transacción pasa a alerta (RN05/RN06).

## Flujo de trabajo
Scrum con prácticas XP: ramas `feature/*` hacia `main` mediante pull request, pruebas primero (TDD) e
integración continua en GitHub Actions.
