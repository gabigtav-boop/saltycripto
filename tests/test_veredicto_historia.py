"""«Ventaja modesta» exige que el intervalo excluya el cero tanto en la ventana reciente como en toda la historia diaria."""
import copy

import pytest

from cripto.validacion import con_intervalo, intervalo_ic
from test_indicadores_clasicos import _muestras

SIN = {"X": {"bt": {"SM": _muestras(0)}}}
FUERTE = {"X": {"bt": {"SM": _muestras(.4)}}}
CI_POS = {"lo": .05, "hi": .2}
CI_CERO = {"lo": -.03, "hi": .05}


def val(vd="AL"):
    return {"vd": [vd, "Ventaja modesta" if vd == "AL" else "x"], "icAll": .1}


def test_se_mantiene_solo_si_reciente_y_larga_excluyen_el_cero():
    v = con_intervalo(val(), FUERTE, CI_POS)
    assert v["vd"] == ["AL", "Ventaja modesta"] and v["ic_larga"] == CI_POS and v["ic_int"]["lo"] > 0


def test_historia_completa_que_incluye_el_cero_degrada():
    v = con_intervalo(val(), FUERTE, CI_CERO)
    assert v["vd"][0] == "NE" and "no confirmada con toda la historia" in v["vd"][1]


def test_sin_historia_completa_no_se_puede_confirmar():
    v = con_intervalo(val(), FUERTE, None)
    assert v["vd"][0] == "NE" and v["ic_larga"] is None


def test_ventana_reciente_que_incluye_el_cero_degrada_aunque_la_larga_sea_positiva():
    v = con_intervalo(val(), SIN, CI_POS)
    assert v["vd"] == ["NE", "Ventaja posible, no demostrada"]


@pytest.mark.parametrize("vd", ["NE", "BA", "ND"])
def test_otros_veredictos_no_se_tocan(vd):
    v = con_intervalo(val(vd), FUERTE, CI_CERO)
    assert v["vd"][0] == vd and v["vd"][1] == "x"


def test_sin_validacion_devuelve_lo_mismo():
    assert con_intervalo(None, FUERTE, CI_POS) is None


def test_ventana_dias_distingue_corta_y_larga():
    corta = intervalo_ic(FUERTE)
    larga = intervalo_ic({"X": {"bt": {"SM": _muestras(.4, n=1000)}}})
    assert larga["ventana_dias"] > corta["ventana_dias"] == 600


def test_el_servicio_calcula_la_historia_completa_una_vez_por_vela(monkeypatch):
    from cripto import servicio as sv_mod
    from cripto.servicio import Servicio
    s = Servicio(net=False)
    raw = [{"t": i * 86400000, "o": 100 + i % 7, "h": 103 + i % 7, "l": 98 + i % 7, "c": 100 + (i * 3) % 11, "v": 5.0} for i in range(500)]
    s.data = {"BTC": {"raw1d": raw[-300:]}, "ETH": None, "XRP": {}}
    llamadas = []
    real = sv_mod.backtest
    monkeypatch.setattr(s.hist, "serie", lambda sym, tf, kr: raw)
    monkeypatch.setattr(sv_mod, "backtest", lambda r: (llamadas.append(1), real(r))[1])
    a = s.muestras_largas()
    assert list(a) == ["BTC"] and len(a["BTC"]["bt"]["SM"]) > 100
    s.muestras_largas()
    assert len(llamadas) == 1                          # misma vela: no se recalcula
    raw.append(dict(raw[-1], t=raw[-1]["t"] + 86400000))
    s.muestras_largas()
    assert len(llamadas) == 2                          # vela nueva: se recalcula


def test_un_fallo_en_la_historia_larga_no_rompe_la_validacion(monkeypatch):
    from cripto.servicio import Servicio
    s = Servicio(net=False)
    s.data = {"BTC": {"raw1d": [{"t": 1}]}}
    monkeypatch.setattr(s.hist, "serie", lambda *a: (_ for _ in ()).throw(OSError("disco")))
    assert s.muestras_largas() == {}
    s.do_validate()                                    # validate(...) sin datos de bt devuelve None; no debe lanzar
    assert s.val is None
