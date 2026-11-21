"""scripts/sembrar_puc.py: la opción --forzar y el informe que imprime (sin base de datos)."""

import importlib.util
from pathlib import Path

import pytest

from agente.infrastructure.db.repositorio import ResumenSiembra

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sembrar_puc.py"


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("sembrar_puc", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    llamadas: list[bool] = []
    sincronizaciones: list[bool] = []

    def sembrar_falso(_directorio, forzar=False, sincronizar=False):
        llamadas.append(forzar)
        sincronizaciones.append(sincronizar)
        return ResumenSiembra(
            cuentas_insertadas=2,
            cuentas_sobrescritas=1 if forzar else 0,
            cuentas_omitidas=40,
            reglas_insertadas=1,
            reglas_sobrescritas=3 if forzar else 0,
            reglas_omitidas=4,
            reglas_desactivadas=["R-VIEJA-1", "R-VIEJA-2"] if sincronizar else [],
        )

    monkeypatch.setattr(modulo, "sembrar_desde_csv", sembrar_falso)
    modulo.sincronizaciones = sincronizaciones
    return modulo, llamadas


def test_sin_opciones_no_fuerza(script, capsys):
    modulo, llamadas = script
    modulo.main([])
    assert llamadas == [False]
    salida = capsys.readouterr().out
    assert "Total: 3 filas insertadas, 44 omitidas." in salida
    assert "sobrescritas" not in salida


def test_con_forzar_sobrescribe_e_informa(script, capsys):
    modulo, llamadas = script
    modulo.main(["--forzar"])
    assert llamadas == [True]
    salida = capsys.readouterr().out
    assert "Cuentas: 2 insertadas, 1 sobrescritas, 40 omitidas." in salida
    assert "Reglas: 1 insertadas, 3 sobrescritas, 4 omitidas." in salida
    assert "Total: 3 filas insertadas, 4 sobrescritas, 44 omitidas." in salida


def test_opcion_desconocida_falla(script):
    modulo, _ = script
    with pytest.raises(SystemExit):
        modulo.main(["--borrar-todo"])


def test_sin_sincronizar_no_desactiva_ni_lo_informa(script, capsys):
    modulo, _ = script
    modulo.main([])
    assert modulo.sincronizaciones == [False]
    assert "--sincronizar" not in capsys.readouterr().out


def test_con_sincronizar_informa_las_reglas_desactivadas(script, capsys):
    modulo, llamadas = script
    modulo.main(["--sincronizar"])
    assert (llamadas, modulo.sincronizaciones) == ([False], [True])
    assert "Reglas desactivadas por --sincronizar (2): R-VIEJA-1, R-VIEJA-2." in capsys.readouterr().out

