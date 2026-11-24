"""Jornada contable en hora de Colombia y la variable HORA_CIERRE."""

from datetime import UTC, date, datetime, time

import pytest

from agente.config import get_settings
from agente.domain.jornada import corte_de_la_jornada, en_colombia, fecha_jornada, hora_de_cierre


def test_la_zona_horaria_de_colombia_esta_disponible():
    # En Windows, zoneinfo necesita el paquete tzdata (dependencia explícita en pyproject.toml).
    assert en_colombia(datetime(2026, 11, 24, 12, 0, tzinfo=UTC)).utcoffset().total_seconds() == -5 * 3600


@pytest.mark.parametrize(
    ("instante_utc", "jornada"),
    [
        (datetime(2026, 11, 25, 4, 59, tzinfo=UTC), date(2026, 11, 24)),  # 23:59 en Colombia del 24
        (datetime(2026, 11, 25, 5, 0, tzinfo=UTC), date(2026, 11, 25)),  # 00:00 en Colombia del 25
        (datetime(2026, 11, 24, 23, 0, tzinfo=UTC), date(2026, 11, 24)),  # 18:00 en Colombia
    ],
)
def test_el_dia_se_corta_en_hora_de_colombia_no_en_utc(instante_utc, jornada):
    assert fecha_jornada(instante_utc) == jornada


def test_corte_de_la_jornada_en_utc():
    assert corte_de_la_jornada(date(2026, 11, 24), time(18, 0)) == datetime(2026, 11, 24, 23, 0, tzinfo=UTC)


def test_sin_zona_horaria_es_un_error():
    with pytest.raises(ValueError, match="zona horaria"):
        fecha_jornada(datetime(2026, 11, 24, 12, 0))


@pytest.mark.parametrize(("texto", "hora"), [("18:00", time(18, 0)), ("7:05", time(7, 5)), (" 23:59 ", time(23, 59))])
def test_hora_de_cierre_valida(texto, hora):
    assert hora_de_cierre(texto) == hora


@pytest.mark.parametrize("texto", ["24:00", "18", "18:60", "6pm", ""])
def test_hora_de_cierre_invalida(texto):
    with pytest.raises(ValueError, match="HORA_CIERRE"):
        hora_de_cierre(texto)


def test_hora_cierre_por_defecto_y_desde_el_entorno(monkeypatch):
    monkeypatch.delenv("HORA_CIERRE", raising=False)
    get_settings.cache_clear()
    try:
        por_defecto = get_settings().hora_cierre
        monkeypatch.setenv("HORA_CIERRE", "17:30")
        get_settings.cache_clear()
        assert get_settings().hora_cierre == time(17, 30)
    finally:
        get_settings.cache_clear()
    # El .env local puede fijarla; sin .env ni variable, 18:00.
    assert isinstance(por_defecto, time)
