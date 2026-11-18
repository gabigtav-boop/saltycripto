"""Integración (auditoría 17 nov 2026): el ciclo en vivo con datos reales capturados no se para por una previsión defectuosa
de una moneda, y el estado que recibe la interfaz es JSON estricto con los campos nuevos de la previsión."""
import json
import pathlib

import pytest

from cripto import servicio as sv_mod
from cripto.servicio import COINS, Servicio

from test_paridad import FIX, FakeKraken  # noqa: E402  (pytest añade tests/ al camino de importación)


@pytest.fixture(scope="module")
def fx():
    return json.loads(pathlib.Path(FIX).read_text())


def _servicio(fx, monedas, monkeypatch):
    sv = Servicio(kraken=FakeKraken(fx), net=False)
    sv.pairs, sv.tk = fx["pairs"], fx["ticker"]
    sv.tk_t = 0
    for s in monedas:
        sv.data[s] = sv.run(s, now=fx["now"])
        # ETH y XRP: en la foto real el último negociado está fuera de la horquilla del ticker REST, así que no se da por
        # vigente sin la confirmación del libro en vivo (18 nov 2026); aquí se simula esa confirmación
        sv.data[s]["_precio_t"] = sv.data[s]["_precio_t"] or fx["now"]
    monkeypatch.setattr(sv_mod, "now_ms", lambda: fx["now"] + 1000)
    return sv


def test_una_prevision_que_falla_no_para_el_ciclo_de_las_demas_monedas(fx, monkeypatch, capsys):
    monedas = [s for s in COINS if fx["pairs"].get(s)][:3]
    sv = _servicio(fx, monedas, monkeypatch)
    llamadas = []
    real = sv_mod.prevision

    def a_veces_falla(o, *a, **k):
        llamadas.append(o["sym"])
        if o["sym"] == monedas[0]:
            raise KeyError("dato corrupto")
        return real(o, *a, **k)
    monkeypatch.setattr(sv_mod, "prevision", a_veces_falla)
    sv.tiempo_real()
    assert llamadas == monedas, "se intentó la previsión de todas las monedas aunque la primera falló"
    assert not sv.data[monedas[0]].get("prev")
    for s in monedas[1:]:
        assert sv.data[s]["prev"]["n_factores"] >= 3 and sv.data[s].get("proy") is not None
    assert "previsión" in capsys.readouterr().out


def test_la_ultima_prevision_valida_se_conserva_marcada_como_obsoleta(fx, monkeypatch):
    monedas = [s for s in COINS if fx["pairs"].get(s)][:1]
    sv = _servicio(fx, monedas, monkeypatch)
    sv.tiempo_real()
    assert sv.data[monedas[0]]["prev"] and not sv.data[monedas[0]]["prev"].get("obsoleta")
    monkeypatch.setattr(sv_mod, "prevision", lambda *a, **k: (_ for _ in ()).throw(ValueError("fuente rota")))
    sv.tiempo_real()
    assert sv.data[monedas[0]]["prev"]["obsoleta"] is True


def test_el_estado_para_la_interfaz_es_json_estricto_con_los_campos_nuevos(fx, monkeypatch):
    monedas = [s for s in COINS if fx["pairs"].get(s)][:2]
    sv = _servicio(fx, monedas, monkeypatch)
    sv.pulso = {monedas[0]: {"score": float("nan"), "n": 3, "n_lectura": 3, "lectura": "Neutral"}}      # dato corrupto de noticias
    sv.tiempo_real()
    p = sv.data[monedas[0]]["prev"]
    assert "Noticias 24 h" in p["faltan"] and p["prob_calibrada"] is None
    st = json.dumps(sv_mod.clean(p), allow_nan=False)                 # la interfaz parsea con JSON.parse: sin NaN
    for k in ("score", "dir", "conf", "acuerdo", "factores", "objetivo", "invalidacion", "cobertura", "n_factores", "faltan"):
        assert k in json.loads(st)
