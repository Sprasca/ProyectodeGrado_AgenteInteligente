"""API de cuentas y reglas (RF09) con RepositorioCatalogoMemoria: sin PostgreSQL ni Groq."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from repositorio_catalogo_memoria import RepositorioCatalogoMemoria
from repositorio_memoria import RepositorioMemoria
from sqlalchemy.exc import OperationalError

import agente.application.clasificar_con_nlp as aplicacion
from agente.api.main import app, get_repositorio, get_repositorio_catalogo
from agente.config import Settings, get_settings
from agente.domain.puc import CatalogoPUC
from agente.infrastructure.db.repositorio import cargar_catalogo_bd
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError

XML = (Path(__file__).resolve().parents[1] / "fixtures" / "factura_comercio_contado.xml").read_bytes()


@pytest.fixture
def repo_catalogo(catalogo):
    # Igual que en la API: cada cambio invalida la caché del catálogo de clasificación.
    return RepositorioCatalogoMemoria.desde_catalogo(catalogo, al_cambiar=aplicacion.invalidar_catalogo)


@pytest.fixture
def cliente(repo_catalogo, catalogo):
    app.dependency_overrides[get_repositorio_catalogo] = lambda: repo_catalogo
    app.dependency_overrides[get_repositorio] = lambda: RepositorioMemoria(catalogo)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _regla(codigo="R-NUEVA", cuenta="4135", palabras=("abarrote",), descripcion="Nueva"):
    return {"codigo": codigo, "cuenta_ingreso": cuenta, "palabras_clave": list(palabras), "descripcion": descripcion}


# --- /catalogo/cuentas ------------------------------------------------------------------


def test_listar_cuentas(cliente):
    r = cliente.get("/catalogo/cuentas")
    assert r.status_code == 200
    por_codigo = {c["codigo"]: c for c in r.json()}
    assert por_codigo["4135"] == {"codigo": "4135", "nombre": "COMERCIO AL POR MAYOR", "naturaleza": "CREDITO"}


def test_crear_cuenta_201(cliente):
    r = cliente.post("/catalogo/cuentas", json={"codigo": "4199", "nombre": "Otros ingresos", "naturaleza": "CREDITO"})
    assert r.status_code == 201
    assert r.json() == {"codigo": "4199", "nombre": "Otros ingresos", "naturaleza": "CREDITO"}
    assert "4199" in {c["codigo"] for c in cliente.get("/catalogo/cuentas").json()}


def test_crear_cuenta_repetida_409(cliente):
    r = cliente.post("/catalogo/cuentas", json={"codigo": "4135", "nombre": "Otra", "naturaleza": "CREDITO"})
    assert r.status_code == 409
    assert "4135" in r.json()["detail"]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"codigo": "41A9", "nombre": "Letras", "naturaleza": "CREDITO"},
        {"codigo": "4199", "nombre": " ", "naturaleza": "CREDITO"},
        {"codigo": "4199", "nombre": "x", "naturaleza": "OTRA"},
        {"codigo": "4199", "nombre": "x"},
        {"codigo": "4199", "nombre": "x", "naturaleza": "CREDITO", "activa": True},
    ],
)
def test_crear_cuenta_invalida_422(cliente, cuerpo):
    assert cliente.post("/catalogo/cuentas", json=cuerpo).status_code == 422


def test_crear_cuenta_invalida_devuelve_los_errores(cliente):
    r = cliente.post("/catalogo/cuentas", json={"codigo": "41A9", "nombre": " ", "naturaleza": "CREDITO"})
    assert len(r.json()["errores"]) == 2


def test_no_hay_borrado_de_cuentas(cliente):
    assert cliente.delete("/catalogo/cuentas/4135").status_code in (404, 405)


# --- GET /reglas ------------------------------------------------------------------------


def test_listar_reglas_y_filtrar(cliente):
    cliente.post("/reglas/R-4170-SERVICIOS/desactivar")
    todas = cliente.get("/reglas")
    assert todas.status_code == 200
    assert {r["codigo"] for r in todas.json()} >= {"R-4135-COMERCIO", "R-4170-SERVICIOS"}

    activas = {r["codigo"] for r in cliente.get("/reglas", params={"activa": "true"}).json()}
    inactivas = {r["codigo"] for r in cliente.get("/reglas", params={"activa": "false"}).json()}
    assert "R-4170-SERVICIOS" in inactivas and "R-4170-SERVICIOS" not in activas
    assert "R-4135-COMERCIO" in activas


def test_obtener_regla(cliente):
    r = cliente.get("/reglas/R-4145-TRANSPORTE")
    assert r.status_code == 200
    datos = r.json()
    assert datos["cuenta_ingreso"] == "4145"
    assert "flete" in datos["palabras_clave"]
    assert datos["activa"] is True


def test_obtener_regla_inexistente_404(cliente):
    assert cliente.get("/reglas/R-NO-EXISTE").status_code == 404


# --- POST /reglas -----------------------------------------------------------------------


def test_crear_regla_201(cliente):
    r = cliente.post("/reglas", json=_regla(palabras=("Abarrotes Finos", "abarrotes finos")))
    assert r.status_code == 201
    datos = r.json()
    assert datos["codigo"] == "R-NUEVA"
    assert datos["palabras_clave"] == ["abarrotes finos"]
    assert datos["activa"] is True
    assert datos["advertencias"] == []
    assert cliente.get("/reglas/R-NUEVA").status_code == 200


@pytest.mark.parametrize(
    ("cuerpo", "fragmento"),
    [
        (_regla(codigo="  "), "vacío"),
        (_regla(codigo="R-4135-COMERCIO"), "Ya existe"),
        (_regla(palabras=()), "palabra clave"),
        (_regla(cuenta="9999"), "no existe"),
        (_regla(cuenta="1105"), "clase 4"),
    ],
)
def test_crear_regla_con_errores_422(cliente, cuerpo, fragmento):
    r = cliente.post("/reglas", json=cuerpo, params={"confirmar": "true"})  # confirmar no salta errores
    assert r.status_code == 422
    assert any(fragmento in e for e in r.json()["errores"])


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"codigo": "R-X", "palabras_clave": ["a"]},
        {**_regla(), "palabras_clave": "no-es-lista"},
        {**_regla(), "activa": False},
    ],
)
def test_crear_regla_cuerpo_mal_formado_422(cliente, cuerpo):
    assert cliente.post("/reglas", json=cuerpo).status_code == 422


def test_crear_regla_con_conflicto_sin_confirmar_409_y_no_guarda(cliente):
    r = cliente.post("/reglas", json=_regla(palabras=("Flete", "abarrote")))
    assert r.status_code == 409
    (advertencia,) = r.json()["advertencias"]
    assert (advertencia["palabra"], advertencia["regla"], advertencia["cuenta"]) == (
        "flete",
        "R-4145-TRANSPORTE",
        "4145",
    )
    assert "R-4145-TRANSPORTE" in advertencia["mensaje"]
    assert cliente.get("/reglas/R-NUEVA").status_code == 404


def test_crear_regla_con_conflicto_confirmado_201_y_queda_en_historial(cliente):
    r = cliente.post("/reglas", json=_regla(palabras=("flete",)), params={"confirmar": "true"})
    assert r.status_code == 201
    assert r.json()["advertencias"][0]["regla"] == "R-4145-TRANSPORTE"
    (entrada,) = cliente.get("/reglas/R-NUEVA/historial").json()
    assert entrada["accion"] == "CREAR"
    assert entrada["despues"]["advertencias_confirmadas"][0]["regla"] == "R-4145-TRANSPORTE"


# --- PUT /reglas/{codigo} -----------------------------------------------------------------


def _edicion(cuenta="4135", palabras=("mercancia", "abarrote"), descripcion="Editada", **extra):
    return {"cuenta_ingreso": cuenta, "palabras_clave": list(palabras), "descripcion": descripcion, **extra}


def test_editar_regla_200(cliente):
    r = cliente.put("/reglas/R-4135-COMERCIO", json=_edicion())
    assert r.status_code == 200
    assert r.json()["palabras_clave"] == ["mercancia", "abarrote"]
    assert r.json()["advertencias"] == []
    assert [e["accion"] for e in cliente.get("/reglas/R-4135-COMERCIO/historial").json()] == ["EDITAR"]


def test_editar_regla_con_el_mismo_codigo_en_el_cuerpo_200(cliente):
    assert cliente.put("/reglas/R-4135-COMERCIO", json=_edicion(codigo="R-4135-COMERCIO")).status_code == 200


def test_editar_regla_no_permite_cambiar_el_codigo_422(cliente):
    r = cliente.put("/reglas/R-4135-COMERCIO", json=_edicion(codigo="R-OTRO"))
    assert r.status_code == 422
    assert "no se puede cambiar" in r.json()["detail"]
    assert cliente.get("/reglas/R-OTRO").status_code == 404


def test_editar_regla_inexistente_404(cliente):
    assert cliente.put("/reglas/R-NO-EXISTE", json=_edicion()).status_code == 404


def test_editar_regla_con_errores_422(cliente):
    r = cliente.put("/reglas/R-4135-COMERCIO", json=_edicion(cuenta="1105", palabras=()))
    assert r.status_code == 422
    assert len(r.json()["errores"]) == 2
    assert cliente.get("/reglas/R-4135-COMERCIO").json()["cuenta_ingreso"] == "4135"


def test_editar_regla_con_conflicto_409_y_luego_confirmado_200(cliente):
    sin_confirmar = cliente.put("/reglas/R-4135-COMERCIO", json=_edicion(palabras=("mercancia", "flete")))
    assert sin_confirmar.status_code == 409
    assert sin_confirmar.json()["advertencias"][0]["palabra"] == "flete"
    assert "flete" not in cliente.get("/reglas/R-4135-COMERCIO").json()["palabras_clave"]

    confirmada = cliente.put(
        "/reglas/R-4135-COMERCIO", json=_edicion(palabras=("mercancia", "flete")), params={"confirmar": "true"}
    )
    assert confirmada.status_code == 200
    assert "flete" in cliente.get("/reglas/R-4135-COMERCIO").json()["palabras_clave"]


# --- activar / desactivar / historial --------------------------------------------------------


def test_desactivar_y_activar_200(cliente):
    r = cliente.post("/reglas/R-4135-COMERCIO/desactivar")
    assert r.status_code == 200 and r.json()["activa"] is False
    r = cliente.post("/reglas/R-4135-COMERCIO/activar")
    assert r.status_code == 200 and r.json()["activa"] is True
    historial = cliente.get("/reglas/R-4135-COMERCIO/historial")
    assert historial.status_code == 200
    assert [e["accion"] for e in historial.json()] == ["DESACTIVAR", "ACTIVAR"]


@pytest.mark.parametrize("ruta", ["/reglas/R-NO-EXISTE/activar", "/reglas/R-NO-EXISTE/desactivar"])
def test_activar_o_desactivar_inexistente_404(cliente, ruta):
    assert cliente.post(ruta).status_code == 404


def test_historial_inexistente_404(cliente):
    assert cliente.get("/reglas/R-NO-EXISTE/historial").status_code == 404


# --- Base de datos caída: 503 en todos los endpoints -----------------------------------------


class RepositorioCatalogoCaido:
    def _caer(self, *_args, **_kwargs):
        raise RepositorioNoDisponibleError("No hay conexión con PostgreSQL (simulado).")

    listar_cuentas = crear_cuenta = listar_reglas = obtener_regla = crear_regla = _caer
    editar_regla = activar_regla = desactivar_regla = historial = _caer


@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("get", "/catalogo/cuentas", None),
        ("post", "/catalogo/cuentas", {"codigo": "4199", "nombre": "x", "naturaleza": "CREDITO"}),
        ("get", "/reglas", None),
        ("get", "/reglas/R-X", None),
        ("post", "/reglas", _regla()),
        ("put", "/reglas/R-X", _edicion()),
        ("post", "/reglas/R-X/activar", None),
        ("post", "/reglas/R-X/desactivar", None),
        ("get", "/reglas/R-X/historial", None),
    ],
)
def test_base_inaccesible_503(metodo, ruta, cuerpo):
    app.dependency_overrides[get_repositorio_catalogo] = RepositorioCatalogoCaido
    try:
        kwargs = {"json": cuerpo} if cuerpo is not None else {}
        r = getattr(TestClient(app), metodo)(ruta, **kwargs)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert "PostgreSQL" in r.json()["detail"]


# --- FUENTE_CATALOGO=bd: caché e invalidación ---------------------------------------------------


@pytest.fixture
def fuente_bd(monkeypatch, repo_catalogo):
    """Simula la base con el repositorio en memoria: cuenta cuántas veces se carga el catálogo."""
    cargas: list[int] = []

    def cargar_desde_bd(_fabrica):
        cargas.append(1)
        return repo_catalogo.como_catalogo()

    monkeypatch.setenv("FUENTE_CATALOGO", "bd")
    monkeypatch.setattr(aplicacion, "cargar_catalogo_bd", cargar_desde_bd)
    monkeypatch.setattr(aplicacion, "fabrica_compartida", lambda: None)
    get_settings.cache_clear()
    aplicacion.invalidar_catalogo()
    yield cargas
    get_settings.cache_clear()
    aplicacion.invalidar_catalogo()


def _clasificar(cliente, xml=XML):
    r = cliente.post(
        "/facturas/clasificar",
        files={"archivo": ("factura.xml", xml, "application/xml")},
        params={"usar_nlp": "false", "guardar": "false"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_tras_un_cambio_la_siguiente_clasificacion_usa_las_reglas_nuevas(cliente, fuente_bd):
    cargas = fuente_bd
    assert _clasificar(cliente)["lineas"][0]["regla"] == "R-4135-COMERCIO"
    assert _clasificar(cliente)["lineas"][0]["regla"] == "R-4135-COMERCIO"
    assert len(cargas) == 1  # la segunda clasificación usó la caché

    assert cliente.post("/reglas/R-4135-COMERCIO/desactivar").status_code == 200
    linea = _clasificar(cliente)["lineas"][0]
    assert linea["estado"] == "SIN_REGLA"
    assert len(cargas) == 2  # el cambio invalidó la caché

    nueva = _regla(codigo="R-MERCANCIA-SERVICIOS", cuenta="4145", palabras=("mercancia",))
    assert cliente.post("/reglas", json=nueva).status_code == 201
    linea = _clasificar(cliente)["lineas"][0]
    assert linea["estado"] == "CLASIFICADA"
    assert (linea["regla"], linea["cuenta_ingreso"]) == ("R-MERCANCIA-SERVICIOS", "4145")


def test_crear_una_cuenta_tambien_invalida_la_cache(cliente, fuente_bd):
    _clasificar(cliente)
    cliente.post("/catalogo/cuentas", json={"codigo": "4199", "nombre": "Otros", "naturaleza": "CREDITO"})
    _clasificar(cliente)
    assert len(fuente_bd) == 2


def test_clasificar_con_fuente_bd_y_base_caida_503(cliente, monkeypatch):
    def caida(_fabrica):
        raise RepositorioNoDisponibleError("No hay conexión con PostgreSQL para cargar el catálogo.")

    monkeypatch.setenv("FUENTE_CATALOGO", "bd")
    monkeypatch.setattr(aplicacion, "cargar_catalogo_bd", caida)
    monkeypatch.setattr(aplicacion, "fabrica_compartida", lambda: None)
    get_settings.cache_clear()
    aplicacion.invalidar_catalogo()
    try:
        r = cliente.post(
            "/facturas/clasificar",
            files={"archivo": ("factura.xml", XML, "application/xml")},
            params={"usar_nlp": "false", "guardar": "false"},
        )
    finally:
        get_settings.cache_clear()
        aplicacion.invalidar_catalogo()
    assert r.status_code == 503
    assert "PostgreSQL" in r.json()["detail"]


# --- FUENTE_CATALOGO y carga desde la base --------------------------------------------------------


def test_las_pruebas_unitarias_usan_csv():
    assert get_settings().fuente_catalogo == "csv"


def test_fuente_por_defecto_es_bd(monkeypatch):
    monkeypatch.delenv("FUENTE_CATALOGO", raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # sin .env en el directorio actual
    assert Settings().fuente_catalogo == "bd"


def test_fuente_invalida_es_error(monkeypatch):
    monkeypatch.setenv("FUENTE_CATALOGO", "excel")
    with pytest.raises(ValueError, match="FUENTE_CATALOGO"):
        Settings()


def test_fuente_csv_lee_data_puc(monkeypatch, catalogo):
    aplicacion.invalidar_catalogo()
    try:
        cargado = aplicacion.catalogo_por_defecto()
    finally:
        aplicacion.invalidar_catalogo()
    assert [c.codigo for c in cargado.cuentas] == [c.codigo for c in catalogo.cuentas]


class _FabricaCaida:
    def __call__(self):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))


def test_cargar_catalogo_bd_sin_conexion_lanza_no_disponible():
    with pytest.raises(RepositorioNoDisponibleError, match="FUENTE_CATALOGO=csv"):
        cargar_catalogo_bd(_FabricaCaida())


def test_cargar_catalogo_bd_vacio_pide_sembrar(monkeypatch):
    import agente.infrastructure.db.repositorio as repositorio

    class _Sesion:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(repositorio, "cargar_catalogo", lambda _sesion: CatalogoPUC([], []))
    with pytest.raises(RepositorioNoDisponibleError, match="sembrar_puc"):
        cargar_catalogo_bd(lambda: _Sesion())
