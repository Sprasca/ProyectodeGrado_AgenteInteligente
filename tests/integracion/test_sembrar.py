"""sembrar_puc contra PostgreSQL real: sin --forzar no sobrescribe; con --forzar sí.

Usa unos CSV temporales con una cuenta y una regla propias de la prueba, para no tocar el
catálogo real de la base. Todo lo creado se borra al final (fixture aislamiento).
"""

import pytest
from sqlalchemy import select

from agente.domain.enums import AccionHistorial
from agente.infrastructure.db.catalogo import RepositorioCatalogoSQL
from agente.infrastructure.db.models import CuentaPUCModel
from agente.infrastructure.db.repositorio import USUARIO_SIEMBRA, sembrar_desde_csv

pytestmark = pytest.mark.integracion


@pytest.fixture
def semilla(tmp_path, aislamiento):
    """Directorio con cuentas_ventas.csv y reglas_ingreso.csv de prueba."""
    cuenta, regla, palabra = aislamiento.cuenta(), aislamiento.codigo(), aislamiento.palabra()
    (tmp_path / "cuentas_ventas.csv").write_text(
        f"codigo,nombre,naturaleza\n{cuenta},Ingresos de la semilla,CREDITO\n", encoding="utf-8"
    )
    (tmp_path / "reglas_ingreso.csv").write_text(
        f"codigo_regla,cuenta_ingreso,palabras_clave,descripcion\n{regla},{cuenta},{palabra},Regla de la semilla\n",
        encoding="utf-8",
    )
    return tmp_path, cuenta, regla, palabra


def _nombre_cuenta(fabrica, codigo):
    with fabrica() as sesion:
        return sesion.scalar(select(CuentaPUCModel.nombre).where(CuentaPUCModel.codigo == codigo))


def _renombrar_cuenta(fabrica, codigo, nombre):
    """No hay endpoint para editar cuentas: se simula una edición hecha directamente en la base."""
    with fabrica() as sesion, sesion.begin():
        sesion.get(CuentaPUCModel, codigo).nombre = nombre


def test_primera_siembra_inserta_y_deja_historial(fabrica_bd, semilla):
    directorio, cuenta, regla, palabra = semilla
    resumen = sembrar_desde_csv(directorio, fabrica_sesiones=fabrica_bd)
    assert (resumen.cuentas_insertadas, resumen.reglas_insertadas) == (1, 1)
    assert (resumen.insertadas, resumen.sobrescritas, resumen.omitidas) == (2, 0, 0)

    repo = RepositorioCatalogoSQL(fabrica_bd)
    guardada = repo.obtener_regla(regla)
    assert (guardada.cuenta_ingreso, guardada.palabras_clave, guardada.activa) == (cuenta, (palabra,), True)
    (entrada,) = repo.historial(regla)
    assert (entrada.accion, entrada.usuario) == (AccionHistorial.CREAR, USUARIO_SIEMBRA)


def test_sin_forzar_no_sobrescribe_lo_que_edito_el_contador(fabrica_bd, semilla, aislamiento):
    directorio, cuenta, regla, _ = semilla
    sembrar_desde_csv(directorio, fabrica_sesiones=fabrica_bd)

    repo = RepositorioCatalogoSQL(fabrica_bd)
    editada = repo.editar_regla(regla, cuenta, [aislamiento.palabra()], "Editada por el contador").regla
    _renombrar_cuenta(fabrica_bd, cuenta, "Nombre del contador")

    resumen = sembrar_desde_csv(directorio, fabrica_sesiones=fabrica_bd)
    assert (resumen.insertadas, resumen.sobrescritas, resumen.omitidas) == (0, 0, 2)
    assert repo.obtener_regla(regla) == editada
    assert _nombre_cuenta(fabrica_bd, cuenta) == "Nombre del contador"
    assert [e.accion for e in repo.historial(regla)] == [AccionHistorial.CREAR, AccionHistorial.EDITAR]


def test_con_forzar_sobrescribe_desde_los_csv(fabrica_bd, semilla, aislamiento):
    directorio, cuenta, regla, palabra = semilla
    sembrar_desde_csv(directorio, fabrica_sesiones=fabrica_bd)

    repo = RepositorioCatalogoSQL(fabrica_bd)
    repo.editar_regla(regla, cuenta, [aislamiento.palabra()], "Editada por el contador")
    repo.desactivar_regla(regla)
    _renombrar_cuenta(fabrica_bd, cuenta, "Nombre del contador")

    resumen = sembrar_desde_csv(directorio, forzar=True, fabrica_sesiones=fabrica_bd)
    assert (resumen.cuentas_sobrescritas, resumen.reglas_sobrescritas) == (1, 1)
    assert (resumen.insertadas, resumen.omitidas) == (0, 0)

    regla_bd = repo.obtener_regla(regla)
    assert (regla_bd.palabras_clave, regla_bd.descripcion) == ((palabra,), "Regla de la semilla")
    assert regla_bd.activa is False  # --forzar no reactiva lo que el contador desactivó
    assert _nombre_cuenta(fabrica_bd, cuenta) == "Ingresos de la semilla"

    ultima = repo.historial(regla)[-1]
    assert (ultima.accion, ultima.usuario) == (AccionHistorial.EDITAR, USUARIO_SIEMBRA)
    assert ultima.antes["descripcion"] == "Editada por el contador"
    assert ultima.despues["descripcion"] == "Regla de la semilla"

    # Lo que ya es igual a los CSV no se vuelve a escribir.
    otra = sembrar_desde_csv(directorio, forzar=True, fabrica_sesiones=fabrica_bd)
    assert (otra.insertadas, otra.sobrescritas, otra.omitidas) == (0, 0, 2)
