"""sembrar_puc --sincronizar contra PostgreSQL real, y el 503 que nombra una regla activa inválida.

Las pruebas de sincronización corren dentro de una transacción que se DESHACE al final: no cambian
nada de la base (ni siquiera las reglas reales que --sincronizar desactivaría).
"""

import pytest
from sqlalchemy import select

from agente.domain.enums import AccionHistorial, OrigenRegla
from agente.domain.puc import CatalogoPUC, ReglaIngreso
from agente.infrastructure.db.models import HistorialReglaModel, ReglaClasificacionModel
from agente.infrastructure.db.repositorio import USUARIO_SIEMBRA, cargar_catalogo_bd, sembrar_catalogo
from agente.infrastructure.db.transacciones import RepositorioNoDisponibleError

pytestmark = pytest.mark.integracion


def _regla(codigo, palabra, origen, activa=True, cuenta="4135"):
    return ReglaClasificacionModel(
        codigo=codigo,
        cuenta_codigo=cuenta,
        palabras_clave=[palabra],
        descripcion="Regla de prueba",
        activa=activa,
        origen=origen.value,
    )


@pytest.fixture
def transaccion_deshecha(fabrica_bd):
    """Sesión cuyos cambios se deshacen al terminar la prueba."""
    with fabrica_bd() as sesion:
        try:
            yield sesion
        finally:
            sesion.rollback()


@pytest.fixture
def escenario(transaccion_deshecha, aislamiento, catalogo):
    """
    - sigue: regla "csv" que sigue en los CSV;
    - obsoleta: regla "csv" activa que ya no está en los CSV;
    - ya_inactiva: regla "csv" que ya no está en los CSV pero ya estaba inactiva;
    - del_contador: regla "contador" activa que no está en los CSV.
    """
    sesion = transaccion_deshecha
    codigos = {n: aislamiento.codigo() for n in ("sigue", "obsoleta", "ya_inactiva", "del_contador")}
    sesion.add_all(
        [
            _regla(codigos["sigue"], aislamiento.palabra(), OrigenRegla.CSV),
            _regla(codigos["obsoleta"], aislamiento.palabra(), OrigenRegla.CSV),
            _regla(codigos["ya_inactiva"], aislamiento.palabra(), OrigenRegla.CSV, activa=False),
            _regla(codigos["del_contador"], aislamiento.palabra(), OrigenRegla.CONTADOR),
        ]
    )
    sesion.flush()
    en_csv = CatalogoPUC(
        list(catalogo.cuentas), [*catalogo.reglas, ReglaIngreso(codigos["sigue"], "4135", ("palabra",))]
    )
    return sesion, codigos, en_csv


def _fila(sesion, codigo):
    return sesion.scalar(select(ReglaClasificacionModel).where(ReglaClasificacionModel.codigo == codigo))


def test_sincronizar_desactiva_las_reglas_csv_que_ya_no_estan(escenario):
    sesion, codigos, en_csv = escenario
    resumen = sembrar_catalogo(sesion, en_csv, sincronizar=True)

    assert codigos["obsoleta"] in resumen.reglas_desactivadas
    assert _fila(sesion, codigos["obsoleta"]).activa is False
    assert _fila(sesion, codigos["sigue"]).activa is True
    assert codigos["ya_inactiva"] not in resumen.reglas_desactivadas

    historial = sesion.scalars(
        select(HistorialReglaModel)
        .where(HistorialReglaModel.regla_codigo == codigos["obsoleta"])
        .order_by(HistorialReglaModel.id)
    ).all()
    assert [(h.accion, h.usuario) for h in historial] == [(AccionHistorial.DESACTIVAR.value, USUARIO_SIEMBRA)]
    assert (historial[0].antes["activa"], historial[0].despues["activa"]) == (True, False)


def test_sincronizar_nunca_toca_reglas_del_contador(escenario):
    sesion, codigos, en_csv = escenario
    resumen = sembrar_catalogo(sesion, en_csv, sincronizar=True)
    assert codigos["del_contador"] not in resumen.reglas_desactivadas
    assert _fila(sesion, codigos["del_contador"]).activa is True
    sin_historial = select(HistorialReglaModel).where(HistorialReglaModel.regla_codigo == codigos["del_contador"])
    assert sesion.scalars(sin_historial).all() == []


def test_solo_desactiva_reglas_csv_activas_que_no_estan_en_los_csv(escenario):
    sesion, _, en_csv = escenario
    resumen = sembrar_catalogo(sesion, en_csv, sincronizar=True)
    codigos_csv = {r.codigo for r in en_csv.reglas}
    for codigo in resumen.reglas_desactivadas:
        fila = _fila(sesion, codigo)
        assert fila.origen == OrigenRegla.CSV.value
        assert codigo not in codigos_csv
        assert fila.activa is False


def test_sin_sincronizar_no_desactiva_nada(escenario):
    sesion, codigos, en_csv = escenario
    resumen = sembrar_catalogo(sesion, en_csv)
    assert resumen.reglas_desactivadas == []
    assert _fila(sesion, codigos["obsoleta"]).activa is True


def test_las_reglas_que_inserta_la_siembra_quedan_con_origen_csv(transaccion_deshecha, aislamiento, catalogo):
    nueva = aislamiento.codigo()
    en_csv = CatalogoPUC(list(catalogo.cuentas), [ReglaIngreso(nueva, "4135", (aislamiento.palabra(),))])
    sembrar_catalogo(transaccion_deshecha, en_csv)
    assert _fila(transaccion_deshecha, nueva).origen == OrigenRegla.CSV.value


def test_nada_de_lo_anterior_quedo_en_la_base(fabrica_bd, aislamiento):
    with fabrica_bd() as sesion:
        # Todo lo que crean estas pruebas usa el prefijo R-IT... de aislamiento.
        quedaron = sesion.scalars(
            select(ReglaClasificacionModel.codigo).where(ReglaClasificacionModel.codigo.like("R-IT%"))
        ).all()
    assert quedaron == []


# --- 503 que nombra la regla activa inválida -----------------------------------------------------------


def test_regla_activa_invalida_503_nombra_la_regla_y_sugiere_sincronizar(fabrica_bd, aislamiento):
    codigo = aislamiento.codigo()
    with fabrica_bd() as sesion, sesion.begin():
        # 1105 (CAJA) no es una cuenta de ingreso: el catálogo de la base queda inválido.
        sesion.add(_regla(codigo, aislamiento.palabra(), OrigenRegla.CSV, cuenta="1105"))
    with pytest.raises(RepositorioNoDisponibleError) as error:
        cargar_catalogo_bd(fabrica_bd)
    mensaje = str(error.value)
    assert codigo in mensaje
    assert "1105" in mensaje
    assert "sembrar_puc.py --sincronizar" in mensaje
    assert "/reglas/{codigo}/desactivar" in mensaje
