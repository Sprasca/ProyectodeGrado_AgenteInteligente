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
| Catálogo y reglas administrados por el contador desde la API, con historial (RF09) | Listo, con pruebas unitarias y de integración |
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
uv run alembic upgrade head              # crea el esquema (migraciones 0001 a 0004)
uv run python scripts/sembrar_puc.py     # carga las cuentas y reglas semilla que falten
```

### De dónde sale el catálogo: `FUENTE_CATALOGO`
| Valor | Catálogo con el que se clasifica |
|---|---|
| `bd` (por defecto) | Cuentas y reglas **activas** de PostgreSQL, que el contador administra desde la API (ver *Catálogo y reglas*). Si la base no responde, o está vacía, la API responde **503**. |
| `csv` | Los CSV de `data/puc/`, como antes. Lo usan las pruebas unitarias; la demo web siempre lee los CSV. |

El catálogo queda en caché y se invalida en cuanto la API crea una cuenta o crea, edita, activa o desactiva una regla: la siguiente clasificación ya usa el cambio, sin reiniciar. Los cambios hechos **fuera** de la API (otro proceso, `sembrar_puc.py` o SQL directo) solo se ven al reiniciarla.

### `scripts/sembrar_puc.py`
`data/puc/` es solo la **semilla** inicial; con `FUENTE_CATALOGO=bd` la fuente de verdad es la base.

```bash
uv run python scripts/sembrar_puc.py            # inserta solo las cuentas y reglas que no existan
uv run python scripts/sembrar_puc.py --forzar   # sobrescribe las existentes con lo de los CSV
```
- Sin opciones **no sobrescribe nada**: lo que el contador editó en la base se conserva. Al terminar informa cuántas filas insertó y cuántas omitió.
- `--forzar` reemplaza nombre y naturaleza de las cuentas, y cuenta, palabras clave y descripción de las reglas, con lo de los CSV (también informa cuántas sobrescribió). **Pierde las ediciones del contador**. No cambia si una regla está activa o no.
- Cada regla insertada o sobrescrita queda en el historial con el usuario `scripts/sembrar_puc.py`.
- El script no borra: una regla que se quite de los CSV sigue en la base. Para que deje de usarse, desactívela con `POST /reglas/{codigo}/desactivar`.

Si una partida usa una cuenta que no está en la base, guardar o corregir la transacción responde **409**: `La cuenta X no está en la base de datos. Ejecute scripts/sembrar_puc.py.`

## API
```bash
uv run uvicorn agente.api.main:app --reload
```
Documentación interactiva en http://127.0.0.1:8000/docs.

| Método y ruta | Descripción |
|---|---|
| `GET /salud` | Comprobación de vida |
| `POST /facturas/clasificar?usar_nlp=true&guardar=true` | Clasifica un `.xml` o `.pdf`. Con `guardar=true` lo registra y devuelve su `id` (409 si la factura ya existe o si una cuenta no está sembrada, 503 si la base no responde). Una factura sin número recibe `SIN-NUMERO-xxxxxxxx` y queda `EN_ALERTA` |
| `POST /carpeta/procesar` | Clasifica todos los archivos de una carpeta (no guarda) |
| `GET /transacciones?estado=EN_ALERTA` | Lista transacciones, con filtro opcional por estado |
| `GET /transacciones/{id}` | Transacción con sus partidas, el detalle por línea y, si fue corregida, `asiento_propuesto` (lo que propuso el agente) y `corregido_en` |
| `POST /transacciones/{id}/aprobar` | El contador aprueba una transacción `EN_ALERTA` con asiento propuesto |
| `POST /transacciones/{id}/corregir` | El contador reemplaza las partidas: `{"partidas": [{"cuenta": "1105", "naturaleza": "DEBITO", "valor": 119000}, ...]}` |

### Catálogo y reglas (RF09)
El contador administra cuentas y reglas sin tocar código. Cada creación, edición, activación o desactivación de una regla queda en `historial_regla` (acción, antes, después, usuario y fecha; el usuario queda vacío hasta que llegue la autenticación en la Fase 2).

| Método y ruta | Descripción |
|---|---|
| `GET /catalogo/cuentas` | Lista las cuentas |
| `POST /catalogo/cuentas` | Crea una cuenta: `{"codigo": "4199", "nombre": "Otros ingresos", "naturaleza": "CREDITO"}`. **201**; código repetido → **409**; datos inválidos → **422**. No hay borrado de cuentas |
| `GET /reglas?activa=true\|false` | Lista las reglas, con filtro opcional |
| `GET /reglas/{codigo}` | Una regla (**404** si no existe) |
| `POST /reglas?confirmar=false` | Crea una regla: `{"codigo": "R-4140-CAFE", "cuenta_ingreso": "4140", "palabras_clave": ["cafe", "tinto"], "descripcion": "..."}` (**201**) |
| `PUT /reglas/{codigo}?confirmar=false` | Reemplaza cuenta, palabras clave y descripción. El código no se puede cambiar (**422** si el cuerpo trae otro) |
| `POST /reglas/{codigo}/activar` y `/desactivar` | Cambia el estado. Las reglas inactivas no se usan para clasificar |
| `GET /reglas/{codigo}/historial` | Cambios de la regla, del más antiguo al más reciente |

Al crear o editar una regla:
- Las palabras clave se guardan normalizadas (minúsculas, sin tildes) y sin repetidos.
- **422** con la lista `errores` si el código está vacío o repetido, no hay palabras clave, o la cuenta no existe o no es de clase 4 con naturaleza CREDITO.
- **409** con la lista `advertencias` si alguna palabra clave ya está en **otra regla activa que apunta a una cuenta distinta** (y con cuál choca). En ese caso **no se guarda**; para guardarla de todas formas repita la solicitud con `confirmar=true`, y las advertencias quedan en el historial.

## Pruebas
```bash
uv run pytest                    # unitarias: no requieren red, Groq ni PostgreSQL
uv run pytest -m integracion     # integración contra PostgreSQL real (DATABASE_URL)
```
Las pruebas unitarias fijan `FUENTE_CATALOGO=csv`. Las de integración necesitan la base levantada, migrada y sembrada (ver *Puesta en marcha*); si no hay conexión se saltan con un aviso. Crean sus propias reglas y cuentas con códigos únicos y las borran al terminar.

## Integración continua
Cada push y pull request ejecuta [.github/workflows/pruebas.yml](.github/workflows/pruebas.yml) en GitHub Actions. El job levanta un servicio PostgreSQL (`pgvector/pgvector:pg16`, usuario, contraseña y base `agente`), define `DATABASE_URL` y corre: `uv sync`, `uv run ruff check .`, `uv run pytest`, `uv run alembic upgrade head`, `uv run python scripts/sembrar_puc.py` y `uv run pytest -m integracion`. Un cambio en `data/puc/` que deje el catálogo inconsistente (códigos repetidos o reglas que apunten a cuentas que no son de ingreso) hace fallar el job.

## Demo web
`uv run python scripts/empaquetar_demo.py` regenera `docs/agente_demo.zip`, que `docs/index.html` ejecuta en el navegador con Pyodide (solo el motor de reglas).
