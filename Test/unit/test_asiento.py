from decimal import Decimal

import pytest

from agente.domain.asiento import AsientoContable, Partida
from agente.domain.enums import Naturaleza
from agente.domain.errors import AsientoInvalidoError

D, C = Naturaleza.DEBITO, Naturaleza.CREDITO


def test_asiento_balanceado():
    a = AsientoContable(
        (
            Partida("1105", D, Decimal("119.00")),
            Partida("4135", C, Decimal("100.00")),
            Partida("2408", C, Decimal("19.00")),
        )
    )
    assert a.total_debitos == a.total_creditos == Decimal("119.00")


def test_asiento_descuadrado_se_rechaza():
    with pytest.raises(AsientoInvalidoError, match="descuadrado"):
        AsientoContable((Partida("1105", D, Decimal("100")), Partida("4135", C, Decimal("99"))))


def test_requiere_dos_partidas():
    with pytest.raises(AsientoInvalidoError):
        AsientoContable((Partida("1105", D, Decimal("100")),))


def test_valor_no_positivo_se_rechaza():
    with pytest.raises(AsientoInvalidoError, match="no positivo"):
        AsientoContable((Partida("1105", D, Decimal("0")), Partida("4135", C, Decimal("0"))))