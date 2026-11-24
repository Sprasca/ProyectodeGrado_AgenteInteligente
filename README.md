# Agente inteligente para automatizar el registro de ventas bajo el PUC

Proyecto de grado, Ingeniería de Sistemas, Universidad de Cartagena.

El agente recibe la factura electrónica de venta (XML UBL de la DIAN), la interpreta, propone el asiento contable bajo el PUC del Grupo 2 y deja en alerta lo que no clasifica con suficiente confianza.

## Estado: Fase 3 (recepción automática, RF01) En Pruebas

| Componente | Estado |
|---|---|
| Dominio: partida doble, catálogo PUC, motor de reglas | Listo, con pruebas |
| Emparejamiento por palabra completa, puntaje y detección de líneas ambiguas | Listo, con pruebas |
| Parser UBL 2.1 (Invoice y AttachedDocument, con CUFE) | Listo, con pruebas |
| Lector de PDF con texto embebido (heurísticas locales) | Listo, con pruebas |
| PostgreSQL + pgvector, migraciones y carga del catálogo | Listo |
| Catálogo y reglas administrados por el contador desde la API, con historial (RF09) | Listo, con pruebas unitarias y de integración |
| Persistencia de transacciones (guardar, listar, aprobar, corregir, rechazar) | Listo, con pruebas unitarias y de integración |
| Usuarios con roles, autenticación OAuth2 + JWT y permisos por rol | Listo, con pruebas unitarias y de integración |
| Trazabilidad de cada transacción (eventos con usuario y motivo) y documento original con SHA-256 | Listo, con pruebas unitarias y de integración |
| Recepción automática desde `DIRECTORIO_ENTRADA` con watchdog (RF01) | Listo, con pruebas unitarias y de integración |
| Caso de uso `clasificar_hibrido` (reglas + Groq para líneas sin regla) | Listo, con pruebas |
| API HTTP (FastAPI) | Listo, con pruebas |
| NLP + RAG con pgvector | Sprint 2, NLP en proceso de prueba |
| Reporte diario, exportación al ERP | Sprint 3 |

> El catálogo de `data/puc/` es una **semilla** de cuentas de ingresos por ventas. Las facturas de `tests/fixtures/` son sintéticas, no documentos reales. La carpeta de facturas almacena documentos sintéticos y reales que se usan como ejemplo para las pruebas.

## Requisitos
- Python 3.12 y [uv](https://docs.astral.sh/uv/)
- Docker (para PostgreSQL con pgvector)

## Puesta en marcha
```bash
cp .env.example .env
# Defina JWT_SECRETO en .env (obligatorio, al menos 32 caracteres):
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
uv sync
docker compose up -d db
uv run alembic upgrade head                          # crea el esquema (migraciones 0001 a 0008)
uv run python scripts/sembrar_puc.py --sincronizar   # carga la semilla y retira reglas obsoletas
```

### Configuración (`.env`)
| Variable | Para qué sirve |
|---|---|
| `DATABASE_URL` | Conexión a PostgreSQL |
| `JWT_SECRETO` | Firma de los tokens. **Obligatorio**: sin él (o con menos de 32 caracteres) la API no arranca |
| `JWT_MINUTOS` | Vigencia del token, en minutos (60 por defecto) |
| `FUENTE_CATALOGO` | `bd` (por defecto) o `csv` (ver abajo) |
| `CORS_ORIGENES` | Orígenes del navegador que pueden llamar la API, separados por comas. **Vacío por defecto**: ninguno. No acepta `*` |
| `DIRECTORIO_FACTURAS` | Carpeta que procesa `/carpeta/procesar` si no se indica otra. En `.env.example`, `./entrada`: una carpeta local fuera de git (solo se versiona `entrada/.gitkeep`). `facturas/` guarda solo los ejemplos de las pruebas |
| `DIRECTORIO_ENTRADA` | Directorio que vigila la recepción automática (RF01). `/carpeta/procesar` solo acepta carpetas dentro de él (por defecto, `DIRECTORIO_FACTURAS`); una ruta fuera, incluso con `..`, responde **403** |
| `DIRECTORIO_RECIBIDOS`, `DIRECTORIO_NO_RECIBIDOS` | Adónde mueve la recepción automática los archivos procesados y los que fallan (por defecto, `recibidos/` y `no_recibidos/` dentro de `DIRECTORIO_ENTRADA`) |
| `RECEPCION_REINTENTOS`, `RECEPCION_ESPERA_INICIAL` | Reintentos ante una causa temporal (3) y espera antes del primero, en segundos (1); la espera se duplica en cada reintento |
| `RECEPCION_ESPERA_ESTABILIDAD` | Segundos que un archivo debe dejar de cambiar para considerar terminada su copia (1) |
| `TAMANO_MAXIMO_DOCUMENTO_MB` | Tamaño máximo de un documento de factura, en MB (10). En la API, uno mayor responde **413** |
| `UMBRAL_CONFIANZA`, `GROQ_API_KEY`, `GROQ_MODELO` | Clasificación y sugerencias de Groq |

### De dónde sale el catálogo: `FUENTE_CATALOGO`
| Valor | Catálogo con el que se clasifica |
|---|---|
| `bd` (por defecto) | Cuentas y reglas **activas** de PostgreSQL, que el contador administra desde la API (ver *Catálogo y reglas*). Si la base no responde, o está vacía, la API responde **503**. Si una regla activa es inválida, el 503 la nombra |
| `csv` | Los CSV de `data/puc/`. Lo usan las pruebas unitarias; la demo web siempre lee los CSV |

El catálogo queda en caché y se invalida en cuanto la API crea una cuenta o crea, edita, activa o desactiva una regla: la siguiente clasificación ya usa el cambio, sin reiniciar. Los cambios hechos **fuera** de la API (otro proceso, `sembrar_puc.py` o SQL directo) solo se ven al reiniciarla.

### `scripts/sembrar_puc.py`
`data/puc/` es solo la **semilla** inicial; con `FUENTE_CATALOGO=bd` la fuente de verdad es la base.

```bash
uv run python scripts/sembrar_puc.py                # inserta solo las cuentas y reglas que no existan
uv run python scripts/sembrar_puc.py --sincronizar  # además, desactiva las reglas "csv" que ya no están en los CSV
uv run python scripts/sembrar_puc.py --forzar       # sobrescribe las existentes con lo de los CSV
```
- Sin opciones **no sobrescribe nada**: lo que el contador editó en la base se conserva. Al terminar informa cuántas filas insertó y cuántas omitió.
- Cada regla tiene un **origen**: `csv` (la insertó este script) o `contador` (la creó el contador por la API).
- `--sincronizar` desactiva las reglas **activas** de origen `csv` cuyo código ya no está en `reglas_ingreso.csv`, deja `DESACTIVAR` en el historial con el usuario `scripts/sembrar_puc.py` e informa cuáles desactivó. **Nunca toca reglas de origen `contador`** y no borra nada. Ejecútelo cada vez que se quite una regla de los CSV.
- `--forzar` reemplaza nombre y naturaleza de las cuentas, y cuenta, palabras clave y descripción de las reglas, con lo de los CSV (también informa cuántas sobrescribió). **Pierde las ediciones del contador**. No cambia si una regla está activa o no.
- Cada regla insertada o sobrescrita queda en el historial con el usuario `scripts/sembrar_puc.py`.

Si una partida usa una cuenta que no está en la base, guardar o corregir la transacción responde **409**: `La cuenta X no está en la base de datos. Ejecute scripts/sembrar_puc.py.`

## Recepción automática de facturas (RF01)
El sistema de facturación deja las facturas validadas por la DIAN en `DIRECTORIO_ENTRADA`; el agente las detecta y las procesa sin intervención humana.

```bash
uv run python scripts/recibir_facturas.py            # vigila la carpeta hasta Ctrl+C
uv run python scripts/recibir_facturas.py --una-vez  # procesa lo que ya esté en la carpeta y termina
uv run python scripts/recibir_facturas.py --sin-nlp  # clasifica solo con reglas (sin consultar a Groq)
```

Por cada archivo `.xml` o `.pdf` nuevo en el primer nivel de la carpeta:
1. **Espera a que termine la copia**: el tamaño y la fecha del archivo deben dejar de cambiar (`RECEPCION_ESPERA_ESTABILIDAD`).
2. **Valida** que sea un documento completo y legible:
   - XML bien formado, con raíz `Invoice` (o `AttachedDocument` con el `Invoice` embebido) y con `cbc:ID`, `cbc:IssueDate` y al menos una `cac:InvoiceLine`;
   - PDF que empiece con `%PDF-`, termine con `%%EOF` y se pueda abrir;
   - en ambos casos, que no esté vacío ni supere `TAMANO_MAXIMO_DOCUMENTO_MB`.
3. **Lo procesa con el mismo flujo de la carga por la API**: clasificación híbrida y persistencia, con el documento original, sus eventos `RECIBIDA` (canal `carpeta`, **sin usuario**) y `CLASIFICADA`, y el rechazo de duplicados por número, CUFE o SHA-256.
4. **Lo mueve** a `recibidos/` o, si falla, a `no_recibidos/` junto con `<archivo>.motivo.json` (fecha, motivo, tipo de error e intentos; en un duplicado, además, `id_transaccion_existente`: la transacción original). Cada fallo deja además un registro de nivel **ERROR** en el log (la "alerta técnica" del manual). Nunca sobrescribe: si el nombre ya existe, agrega la fecha y la hora.

**Reintentos:** solo ante una causa temporal, es decir, un archivo que aún se copia, está vacío o está bloqueado, o una base de datos que no responde. Se hacen `RECEPCION_REINTENTOS` intentos más, con espera creciente (1, 2, 4… s); agotados, el archivo va a `no_recibidos/`. Un documento inválido, duplicado o demasiado grande va directo a `no_recibidos/`.

**SHA-256 ya registrado:** si en `recibidos/` ya hay un archivo con ese mismo SHA-256, la factura ya se recibió y esta es otra copia: es un duplicado y va a `no_recibidos/` con su `.motivo.json` y un **ERROR** (copiar dos veces el mismo archivo deja uno en cada carpeta). Si no lo hay y la transacción entró por el canal `carpeta` con el mismo nombre de archivo, es una factura que quedó registrada pero no se alcanzó a mover (p. ej. el proceso se detuvo entre el registro y el movimiento): va a `recibidos/` con un **WARNING** y sin crear otra transacción. Cualquier otro SHA-256 repetido (p. ej. registrado por la API) es un duplicado.

**Interrupciones:** cada pocos segundos el vigilante comprueba que la carpeta exista y sea accesible. Si no lo es, deja un ERROR y reanuda cuando vuelve. Al arrancar o al reanudar procesa los archivos que llegaron mientras tanto, del más antiguo al más reciente.

Una factura recibida en **PDF siempre queda `EN_ALERTA`**, en los dos canales (API y carpeta): un PDF no puede validarse contra el anexo técnico de la DIAN, así que nunca se aprueba automáticamente.

**Decisión pendiente con el contador:** exigir el CUFE (`cbc:UUID`) en la validación. Hoy no se exige porque las facturas de prueba no lo traen.

## Usuarios y acceso

### Crear el primer usuario
El primer usuario se crea por consola; la contraseña se pide dos veces, sin mostrarla en pantalla (al menos 8 caracteres). Cree un **ADMIN**, que luego crea a los demás con `POST /usuarios`:
```bash
uv run python scripts/crear_usuario.py admin --nombre-completo "Administradora del sistema" --rol ADMIN
```
Las contraseñas se guardan con Argon2id; el hash nunca sale en las respuestas de la API. El sistema no permite quedarse sin ningún ADMIN activo.

### Obtener el token
```bash
uv run uvicorn agente.api.main:app --reload

curl -X POST http://127.0.0.1:8000/auth/token -d "username=admin" -d "password=<contraseña>"
# → {"access_token": "eyJ...", "token_type": "bearer", "expires_in": 3600}

curl http://127.0.0.1:8000/usuarios -H "Authorization: Bearer eyJ..."
```
En http://127.0.0.1:8000/docs el botón **Authorize** pide usuario y contraseña y envía el token en cada llamada.

Todas las rutas exigen el token, excepto `GET /salud` y `POST /auth/token`. Sin token, con un token vencido o inválido, o de un usuario inactivo → **401**. Con un rol sin permiso → **403**. El rol se lee de la base en cada petición: desactivar un usuario o cambiarle el rol tiene efecto inmediato.

### Permisos por rol
Están definidos en un solo lugar: [src/agente/api/permisos.py](src/agente/api/permisos.py).

| Acción | CONTADOR | AUXILIAR | ADMIN |
|---|:---:|:---:|:---:|
| Cargar facturas (`POST /facturas/clasificar`, `POST /carpeta/procesar`) | | ✓ | |
| Consultar transacciones, eventos, documentos, cuentas y reglas (todos los `GET` de esas rutas) | ✓ | ✓ | |
| Aprobar, corregir y rechazar transacciones | ✓ | | |
| Crear cuentas; crear, editar, activar y desactivar reglas | ✓ | | |
| Usuarios: `GET`, `POST` y `PUT /usuarios` (crear, desactivar, cambiar rol) | | | ✓ |

## API
Documentación interactiva en http://127.0.0.1:8000/docs.

| Método y ruta | Descripción |
|---|---|
| `GET /salud` | Comprobación de vida (sin token) |
| `POST /auth/token` | Inicio de sesión (OAuth2 con contraseña): devuelve el token JWT |
| `POST /facturas/clasificar?usar_nlp=true&guardar=true` | Clasifica un `.xml` o `.pdf`. Con `guardar=true` lo registra con el **documento original** (canal `api`) y devuelve su `id`. Número, **CUFE** o archivo (**SHA-256**) repetido → **409**; más grande que `TAMANO_MAXIMO_DOCUMENTO_MB` → **413**; cuenta no sembrada → **409**; base caída → **503**. Una factura sin número recibe `SIN-NUMERO-xxxxxxxx` y queda `EN_ALERTA` |
| `POST /carpeta/procesar` | Clasifica los archivos de una carpeta dentro de `DIRECTORIO_ENTRADA` (no guarda). Con `recursivo=true` omite `recibidos/` y `no_recibidos/` |
| `GET /transacciones?estado=EN_ALERTA` | Lista transacciones, con filtro opcional por estado |
| `GET /transacciones/{id}` | Transacción con sus partidas, CUFE, detalle por línea y, si fue corregida, `asiento_propuesto` y `corregido_en` |
| `GET /transacciones/{id}/eventos` | Trazabilidad: quién hizo qué, cuándo y por qué |
| `GET /transacciones/{id}/documento` | El archivo original tal como se recibió; su SHA-256 va en la cabecera `X-Contenido-SHA256` |
| `POST /transacciones/{id}/aprobar` | Aprueba una transacción `EN_ALERTA` con asiento propuesto. Cuerpo opcional: `{"motivo": "..."}` |
| `POST /transacciones/{id}/corregir` | Reemplaza las partidas: `{"partidas": [{"cuenta": "1105", "naturaleza": "DEBITO", "valor": 119000}, ...], "motivo": "..."}` |
| `POST /transacciones/{id}/rechazar` | `EN_ALERTA` o `APROBADA_AUTOMATICA` → `RECHAZADA` (estado final): `{"motivo": "..."}` |
| `GET /usuarios`, `POST /usuarios`, `PUT /usuarios/{id}` | Administración de usuarios (ADMIN). `PUT` acepta `rol`, `activo` y `nombre_completo` |

Corregir y rechazar exigen un **motivo de al menos 10 caracteres** (si no, **422**). Una transacción `RECHAZADA` no admite más acciones (**409**).

### Trazabilidad
Cada acción sobre una transacción deja un evento en `evento_transaccion`, en la **misma transacción de base de datos** que el cambio (o queda todo, o no queda nada):

| Evento | Cuándo | Usuario |
|---|---|---|
| `RECIBIDA` | Al guardar la factura (canal `api` o `carpeta`; nombre, tamaño y SHA-256 del archivo) | Quien la cargó; ninguno en la recepción automática |
| `CLASIFICADA` | Al guardar (estado, confianza, reglas y partidas propuestas) | Ninguno: automático |
| `APROBADA`, `CORREGIDA`, `RECHAZADA` | Acciones del contador, con el motivo y el antes/después | El contador |

Los eventos y el documento original no se borran en cascada con la transacción. Los cambios de reglas quedan en `historial_regla` con el usuario autenticado.

### Catálogo y reglas (RF09)
El contador administra cuentas y reglas sin tocar código. Cada creación, edición, activación o desactivación de una regla queda en `historial_regla` (acción, antes, después, usuario y fecha).

| Método y ruta | Descripción |
|---|---|
| `GET /catalogo/cuentas` | Lista las cuentas |
| `POST /catalogo/cuentas` | Crea una cuenta: `{"codigo": "4199", "nombre": "Otros ingresos", "naturaleza": "CREDITO"}`. **201**; código repetido → **409**; datos inválidos → **422**. No hay borrado de cuentas |
| `GET /reglas?activa=true\|false` | Lista las reglas (con su `origen`), con filtro opcional |
| `GET /reglas/{codigo}` | Una regla (**404** si no existe) |
| `POST /reglas?confirmar=false` | Crea una regla (origen `contador`): `{"codigo": "R-4140-CAFE", "cuenta_ingreso": "4140", "palabras_clave": ["cafe", "tinto"], "descripcion": "..."}` (**201**) |
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
Las pruebas unitarias fijan `FUENTE_CATALOGO=csv` y su propio `JWT_SECRETO`, y usan usuarios de prueba en memoria (uno por rol). Las de integración necesitan la base levantada, migrada y sembrada (ver *Puesta en marcha*); si no hay conexión se saltan con un aviso. Crean sus propios usuarios, reglas, cuentas y transacciones con códigos únicos y los borran al terminar.

## Integración continua
Cada push y pull request ejecuta [.github/workflows/pruebas.yml](.github/workflows/pruebas.yml) en GitHub Actions. El job levanta un servicio PostgreSQL (`pgvector/pgvector:pg16`, usuario, contraseña y base `agente`), define `DATABASE_URL` y un `JWT_SECRETO` de prueba, y corre: `uv sync`, `uv run ruff check .`, `uv run pytest`, `uv run alembic upgrade head`, `uv run python scripts/sembrar_puc.py` y `uv run pytest -m integracion`. Un cambio en `data/puc/` que deje el catálogo inconsistente (códigos repetidos o reglas que apunten a cuentas que no son de ingreso) hace fallar el job.

## Demo web
`uv run python scripts/empaquetar_demo.py` regenera `docs/agente_demo.zip`, que `docs/index.html` ejecuta en el navegador con Pyodide (solo el motor de reglas, sin API). El modo "URL de la API" de la demo ya no funciona tal cual: la API exige token y su origen tendría que estar en `CORS_ORIGENES`.
