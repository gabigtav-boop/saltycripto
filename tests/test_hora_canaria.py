"""Hora de Canarias a mano contra la base de zonas horarias, y fechas de noticias sin zona."""
import datetime as dt

import pytest

from cripto import hora
from cripto.noticias import parse_date

zoneinfo = pytest.importorskip("zoneinfo")


def test_coincide_con_atlantic_canary_en_varios_anios_y_en_los_cambios_de_hora():
    tz = zoneinfo.ZoneInfo("Atlantic/Canary")
    t0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    for k in range(0, 3 * 366 * 24 * 4):              # cada 15 min durante 3 años
        t = t0 + k * 900
        esperado = dt.datetime.fromtimestamp(t, tz).replace(tzinfo=None)
        assert hora.a_canarias(t) == esperado, t


def test_cambios_de_hora_de_2026():
    u = lambda *a: int(dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp())          # noqa: E731
    assert hora.hm(u(2026, 3, 29, 0, 59)) == "00:59" and hora.hm(u(2026, 3, 29, 1, 0)) == "02:00"      # empieza el verano
    assert hora.hm(u(2026, 10, 25, 0, 59)) == "01:59" and hora.hm(u(2026, 10, 25, 1, 0)) == "01:00"    # termina
    assert hora.hm(u(2026, 11, 17, 21, 5)) == "21:05" and hora.hms(u(2026, 7, 1, 12, 0, 30)) == "13:00:30"


def test_fechas_de_noticias_sin_zona_se_leen_como_utc_en_cualquier_equipo(monkeypatch):
    import os
    import time
    monkeypatch.setenv("TZ", "Atlantic/Canary")
    time.tzset()
    try:
        esperado = int(dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
        assert parse_date("Wed, 01 Jul 2026 10:00:00 -0000") == esperado
        assert parse_date("2026-07-01T10:00:00") == esperado
        assert parse_date("2026-07-01T10:00:00Z") == esperado
        assert parse_date("Wed, 01 Jul 2026 11:00:00 +0100") == esperado
        assert parse_date("2026-07-01T06:00:00-04:00") == esperado
        assert parse_date("no es una fecha") is None and parse_date("") is None
    finally:
        os.environ.pop("TZ", None)
        time.tzset()
