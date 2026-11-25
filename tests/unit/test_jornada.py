"""Jornada contable en hora de Colombia y horario que define el contador."""

from datetime import UTC, date, datetime, time

import pytest

from agente.domain.errors import HorarioInvalidoError, TransicionInvalidaError
from agente.domain.jornada import (
    HORARIO_INICIAL,
    corte_de_la_jornada,
    en_colombia,
    hora_hhmm,
    jornada_de,
    ultima_jornada_terminada,
    validar_cambio_horario,
    validar_horario,
)

CIERRE = time(18, 0)


@pytest.mark.parametrize(
    ("instante_utc", "jornada"),
    [
        # Colombia es UTC-5 todo el año.
        (datetime(2026, 11, 24, 13, 0, tzinfo=UTC), date(2026, 11, 24)),  # 08:00
        (datetime(2026, 11, 24, 22, 59, 59, tzinfo=UTC), date(2026, 11, 24)),  # 17:59:59: antes del cierre
        (datetime(2026, 11, 24, 23, 0, tzinfo=UTC), date(2026, 11, 25)),  # 18:00 en punto: ya es la siguiente
        (datetime(2026, 11, 25, 2, 0, tzinfo=UTC), date(2026, 11, 25)),  # 21:00 del 24: la siguiente (no por UTC)
        (datetime(2026, 11, 25, 4, 59, tzinfo=UTC), date(2026, 11, 25)),  # 23:59 del 24
        (datetime(2026, 11, 25, 5, 0, tzinfo=UTC), date(2026, 11, 25)),  # 00:00 del 25
    ],
)
def test_jornada_de_lo_recibido_antes_y_despues_de_la_hora_de_cierre(instante_utc, jornada):
    assert jornada_de(instante_utc, CIERRE) == jornada


def test_ultima_jornada_terminada():
    assert ultima_jornada_terminada(datetime(2026, 11, 24, 15, 0, tzinfo=UTC), CIERRE) == date(2026, 11, 23)
    assert ultima_jornada_terminada(datetime(2026, 11, 24, 23, 30, tzinfo=UTC), CIERRE) == date(2026, 11, 24)


def test_corte_de_la_jornada_en_utc():
    assert corte_de_la_jornada(date(2026, 11, 24), CIERRE) == datetime(2026, 11, 24, 23, 0, tzinfo=UTC)
    assert en_colombia(corte_de_la_jornada(date(2026, 11, 24), CIERRE)).hour == 18


def test_sin_zona_horaria_es_un_error():
    with pytest.raises(ValueError, match="zona horaria"):
        jornada_de(datetime(2026, 11, 24, 12, 0), CIERRE)


@pytest.mark.parametrize(("texto", "hora"), [("18:00", time(18, 0)), ("7:05", time(7, 5)), (" 23:59 ", time(23, 59))])
def test_hora_hhmm(texto, hora):
    assert hora_hhmm(texto) == hora


@pytest.mark.parametrize("texto", ["24:00", "18", "18:60", "seis", "", "18:00:00"])
def test_hora_hhmm_invalida(texto):
    with pytest.raises(HorarioInvalidoError, match="HH:MM"):
        hora_hhmm(texto, "La hora de cierre")


def test_horario_inicial_y_apertura_antes_del_cierre():
    assert (HORARIO_INICIAL.hora_apertura, HORARIO_INICIAL.hora_cierre) == (time(8, 0), time(18, 0))
    validar_horario(time(8, 0), time(18, 0))
    for apertura, cierre in [(time(18, 0), time(8, 0)), (time(9, 0), time(9, 0))]:
        with pytest.raises(HorarioInvalidoError, match="anterior a la de cierre"):
            validar_horario(apertura, cierre)


# A las 10:00 de Colombia (15:00 UTC) del 24, con cierre a las 18:00: la jornada en curso es la del 24.
DIEZ_AM = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)


@pytest.mark.parametrize("cierre", [time(17, 0), time(10, 1), time(23, 59)])
def test_el_cierre_se_puede_mover_sin_cambiar_la_jornada_en_curso(cierre):
    validar_cambio_horario(HORARIO_INICIAL, time(7, 0), cierre, DIEZ_AM)


@pytest.mark.parametrize("cierre", [time(9, 0), time(10, 0)])
def test_un_cierre_que_ya_paso_hoy_moveria_la_jornada_en_curso(cierre):
    with pytest.raises(TransicionInvalidaError, match="movería la jornada en curso"):
        validar_cambio_horario(HORARIO_INICIAL, time(7, 0), cierre, DIEZ_AM)


def test_despues_del_cierre_no_se_puede_reabrir_la_jornada():
    a_las_19 = datetime(2026, 11, 25, 0, 0, tzinfo=UTC)  # 19:00 del 24: la jornada en curso ya es la del 25
    with pytest.raises(TransicionInvalidaError):
        validar_cambio_horario(HORARIO_INICIAL, time(8, 0), time(20, 0), a_las_19)
    validar_cambio_horario(HORARIO_INICIAL, time(8, 0), time(17, 0), a_las_19)


def test_cambio_con_apertura_despues_del_cierre_es_422_no_409():
    with pytest.raises(HorarioInvalidoError):
        validar_cambio_horario(HORARIO_INICIAL, time(19, 0), time(18, 0), DIEZ_AM)
