"""Los indicadores contra valores publicados y propiedades que no dependen de la implementación.
El motor es una traducción exacta de la v21 (tests/test_paridad.py): esto comprueba que lo traducido es, además, correcto."""
import math

import pytest

from cripto.indicadores import adx, atr, ema, rsi_s, supertrend
from cripto.validacion import intervalo_ic

# Ejemplo clásico de Wilder (hoja de StockCharts, «RSI»): 14 periodos, suavizado de Wilder
CIERRES = [44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931, 46.0328, 45.6140,
           46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548,
           44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314]
RSI_PUBLICADO = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99, 41.46, 41.87, 45.46,
                 37.30, 33.08, 37.77]


def velas(n, f):
    return [{"t": i * 60000, "o": f(i)[0], "h": f(i)[1], "l": f(i)[2], "c": f(i)[3], "v": 1.0} for i in range(n)]


def test_rsi_coincide_con_el_ejemplo_publicado():
    r = [x for x in rsi_s(CIERRES) if x is not None]
    assert len(r) == len(RSI_PUBLICADO)
    for a, b in zip(r, RSI_PUBLICADO):
        assert a == pytest.approx(b, abs=.006)


def test_rsi_en_los_extremos():
    assert rsi_s(list(range(1, 40)))[-1] == 100                      # solo sube
    assert rsi_s(list(range(40, 1, -1)))[-1] == pytest.approx(0, abs=1e-9)   # solo baja


def test_ema_de_una_serie_constante_y_su_periodo():
    assert ema([5.0] * 30, 12) == pytest.approx([5.0] * 30)
    e = ema([0.0] + [1.0] * 40, 9)
    assert e[1] == pytest.approx(2 / 10)                              # primer paso: factor 2/(p+1)
    assert e[-1] == pytest.approx(1.0, abs=1e-3)


def test_atr_de_velas_de_rango_constante():
    r = velas(60, lambda i: (100, 102, 98, 100))
    assert atr(r) == pytest.approx(4.0)
    assert supertrend(r)["dir"] in (1, -1)


def test_adx_alto_en_tendencia_y_bajo_en_zigzag():
    sube = velas(120, lambda i: (100 + i, 101 + i, 99 + i, 100.5 + i))
    zigzag = velas(120, lambda i: (100, 101 + (i % 2), 99 - (i % 2), 100))
    assert adx(sube) > 40
    assert adx(zigzag) < 20


def _muestras(relacion, n=300, semilla=3):
    import random
    from cripto.motor import score_from
    rnd = random.Random(semilla)
    out = []
    for _ in range(n):
        v = {"ema": rnd.choice((-1, 0, 1)), "macd": rnd.choice((-1, 0, 1)), "obv": rnd.choice((-1, 1))}
        sc = score_from(v, "mix")["score"]
        out.append({"v": v, "rg": "mix", "f": relacion * (sc - 50) / 100 + rnd.gauss(0, .05), "h": False})
    return out


def test_intervalo_de_la_correlacion():
    sin = _muestras(0)
    d = {"X": {"bt": {"SM": sin}}, "Y": {"bt": {"SM": sin[:200]}}}
    a = intervalo_ic(d)
    assert a == intervalo_ic(d)                                       # semilla fija: no cambia entre llamadas
    assert math.isfinite(a["lo"]) and a["lo"] < a["hi"] and a["hi"] - a["lo"] > .02
    fuerte = intervalo_ic({"X": {"bt": {"SM": _muestras(.4)}}})       # relación real: el intervalo excluye el cero
    assert fuerte["lo"] > .3
    assert intervalo_ic({"X": {"bt": {"SM": sin[:20]}}}) is None      # muestra insuficiente: no se inventa
