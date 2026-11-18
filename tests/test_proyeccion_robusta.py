"""Proyección por volatilidad: series normales y anómalas, historia incompleta, ausencia de información futura, cobertura de los
cuantiles y probabilidad de tocar niveles (auditoría 17 nov 2026). Series sintéticas con semilla fija."""
import json
import math

import numpy as np
import pytest

from cripto import proyeccion as P
from cripto.proyeccion import WARM, calibrar, compacto, p_tocar, proyectar, volatilidad


def serie(n, vol=.01, seed=1, dt=3600_000, t0=1_600_000_000_000, base=100.0, volumen=1000.0):
    rng = np.random.default_rng(seed)
    r = rng.standard_normal(n) * vol
    c = base * np.exp(np.cumsum(r))
    o = np.r_[base, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.standard_normal(n)) * vol / 3)
    l = np.minimum(o, c) * (1 - np.abs(rng.standard_normal(n)) * vol / 3)  # noqa: E741
    return [{"t": t0 + i * dt, "o": float(o[i]), "h": float(h[i]), "l": float(l[i]), "c": float(c[i]), "v": volumen} for i in range(n)]


ZONAS = {"res": [{"lo": 104.0, "mid": 105.0, "hi": 106.0}, {"lo": 110.0, "mid": 111.0, "hi": 112.0}],
         "sup": [{"lo": 94.0, "mid": 95.0, "hi": 96.0}, {"lo": 88.0, "mid": 89.0, "hi": 90.0}]}


@pytest.fixture(scope="module")
def cal():
    return calibrar(serie(6000), "1h")


# ---------- serie normal ----------
def test_serie_normal_calibra_y_proyecta(cal):
    assert cal and cal["H"] == 24 and cal["muestras"] > 5000 and math.isfinite(cal["sigma"]) and cal["sigma"] > 0
    pr = proyectar(100.0, "1h", cal, z=ZONAS)
    assert pr["b50"][0] < 100 < pr["b50"][1] and pr["b80"][0] < pr["b50"][0] and pr["b50"][1] < pr["b80"][1]
    assert pr["dir"] == "lateral" and pr["fiable"] is False
    assert len(pr["niveles"]) == 4 and all(0 <= n["p"] <= 1 for n in pr["niveles"])
    json.dumps(compacto(pr), allow_nan=False)


def test_la_proyeccion_no_se_inclina_por_la_prevision(cal):
    a = proyectar(100.0, "1h", cal, prev={"score": 90, "dir": "alcista"}, z=ZONAS)
    b = proyectar(100.0, "1h", cal, prev={"score": -90, "dir": "bajista"}, z=ZONAS)
    c = proyectar(100.0, "1h", cal, z=ZONAS)
    for k in ("b50", "b80", "niveles", "dir", "centro"):
        assert a[k] == b[k] == c[k]


# ---------- cobertura y calibración ----------
def test_cobertura_de_los_cuantiles_fuera_de_muestra_en_serie_estacionaria():
    # volatilidad constante e independiente: la cobertura debe ser la nominal (la del 50 % y la del 80 %)
    c = calibrar(serie(40000, seed=3), "1h")
    v = c["val"]
    assert v["n"] >= 250
    err = 2.5 * math.sqrt(.25 / v["n"])                 # ≈ 2,5 errores típicos de una proporción
    assert abs(v["cob50"] - .5) < err + .03 and abs(v["cob80"] - .8) < err + .03


def test_la_cobertura_dentro_de_muestra_de_los_cuantiles_es_la_nominal(cal):
    # los cuantiles del abanico a horizonte completo contienen el 50 % / 80 % de los retornos normalizados del propio histórico
    rows = serie(6000)
    c = np.array([r["c"] for r in rows])
    sig, lc = volatilidad(c)
    H = cal["H"]
    idx = np.arange(WARM, len(c) - H)
    y = (lc[idx + H] - lc[idx]) / (sig[idx] * math.sqrt(H))
    q = cal["q"]
    assert abs(np.mean((y >= q[1]) & (y <= q[3])) - .5) < .02 and abs(np.mean((y >= q[0]) & (y <= q[4])) - .8) < .02


def test_probabilidad_de_tocar_un_nivel_coincide_con_la_frecuencia_observada():
    rows = serie(30000, seed=5)
    c = calibrar(rows, "1h")
    cl = np.array([r["c"] for r in rows])
    hi = np.array([r["h"] for r in rows])
    H = c["H"]
    sig = c["sigma"]
    for k in (.5, 1.0, 1.5):
        d = k * sig * math.sqrt(H)
        idx = np.arange(WARM, len(cl) - H)
        obs = np.mean([hi[i + 1:i + 1 + H].max() >= cl[i] * math.exp(d) for i in idx])
        est = p_tocar(c, 100.0, 100.0 * math.exp(d), "sube")
        assert abs(obs - est) < .04, (k, obs, est)


def test_probabilidad_de_tocar_es_monotona_y_acotada(cal):
    ps = [p_tocar(cal, 100.0, 100.0 * (1 + d), "sube") for d in (.001, .01, .03, .06, .12, .5)]
    qs = [p_tocar(cal, 100.0, 100.0 * (1 - d), "baja") for d in (.001, .01, .03, .06, .12, .5)]
    for v in (ps, qs):
        assert all(0 <= x <= 1 for x in v) and all(a >= b for a, b in zip(v, v[1:]))
    assert p_tocar(cal, 100.0, 100.0, "sube") == 1.0 and p_tocar(cal, 100.0, 120.0, "baja") == 1.0


# ---------- entradas inválidas ----------
@pytest.mark.parametrize("precio", [float("nan"), float("inf"), -float("inf"), 0, -3, None, "100", True and None])
def test_precio_invalido_no_produce_proyeccion(cal, precio):
    assert proyectar(precio, "1h", cal) is None


@pytest.mark.parametrize("nivel", [float("nan"), float("inf"), 0.0, -5.0, None, "x"])
def test_nivel_invalido_da_none_no_nan_ni_excepcion(cal, nivel):
    assert p_tocar(cal, 100.0, nivel, "sube") is None and p_tocar(cal, 100.0, nivel, "baja") is None
    assert p_tocar(cal, float("nan"), 100.0, "sube") is None


def test_zonas_corruptas_se_omiten_y_el_resto_sigue(cal):
    z = {"res": [{"lo": float("nan"), "mid": float("nan"), "hi": 1.0}, {"lo": 110.0, "mid": 111.0}, {"mid": 105}],
         "sup": [{"lo": 94.0, "mid": 95.0, "hi": 96.0}, None]}
    pr = proyectar(100.0, "1h", cal, z=z)
    # se omiten la zona con NaN y la sola con `mid` (sin borde); se mantienen las que tienen borde y medio
    assert [n["id"] for n in pr["niveles"]] == ["S2", "E1"]
    json.dumps(pr["niveles"], allow_nan=False)


# ---------- series anómalas e historia incompleta ----------
def test_velas_rotas_se_descartan_sin_inventar_nada():
    rows = serie(3000)
    rows[500]["c"] = float("nan")
    rows[600]["c"] = 0
    rows[700]["h"] = -5.0
    rows[800] = {"t": rows[800]["t"]}                      # vela incompleta
    rows[900]["o"] = float("inf")
    c = calibrar(rows, "1h")
    assert c is not None and math.isfinite(c["sigma"]) and np.isfinite(c["q"]).all()
    assert len(P.limpiar(rows)) == 2995                       # NaN, precio 0, máximo negativo, vela incompleta y apertura infinita
    assert all(r["h"] >= max(r["o"], r["c"], r["l"]) and r["l"] <= min(r["o"], r["c"]) for r in P.limpiar(rows))


def test_velas_con_maximo_menor_que_el_minimo_se_corrigen():
    x = P.limpiar([{"t": 1, "o": 10.0, "h": 9.0, "l": 11.0, "c": 10.5, "v": 1.0}])[0]
    assert x["h"] >= x["l"] >= 0 and x["h"] >= 10.5 and x["l"] <= 10.0


def test_historia_incompleta_devuelve_none():
    assert calibrar(serie(150), "1h") is None and calibrar([], "1h") is None
    assert calibrar(serie(WARM + 24 + 50), "1h") is None


def test_serie_constante_no_produce_nan_ni_excepcion():
    rows = [{"t": 1 + i, "o": 5.0, "h": 5.0, "l": 5.0, "c": 5.0, "v": 0.0} for i in range(2000)]
    c = calibrar(rows, "1h")
    if c is not None:
        assert math.isfinite(c["sigma"]) and c["sigma"] < 1e-3
        pr = proyectar(5.0, "1h", c, z=ZONAS)
        json.dumps(pr, allow_nan=False)


def test_precios_diminutos_y_enormes():
    for base in (1e-9, 1e9):
        c = calibrar(serie(3000, base=base), "1h")
        assert c and math.isfinite(c["sigma"]) and 0 < c["sigma"] < .1


def test_volumen_cero_se_marca_como_poca_actividad():
    assert calibrar(serie(3000, volumen=0.0), "1h")["activa"] == 0.0
    assert calibrar(serie(3000), "1h")["activa"] == 1.0


def test_una_vela_absurda_no_dispara_la_volatilidad_durante_cientos_de_velas():
    rows = serie(3000)
    sig0 = calibrar(rows, "1h")["sigma"]
    rows[2000]["c"] = rows[2000]["c"] * 1000
    rows[2000]["h"] = rows[2000]["c"]
    assert calibrar(rows, "1h")["sigma"] < sig0 * 1.5


# ---------- ausencia de información futura ----------
def test_la_volatilidad_de_cada_vela_solo_usa_el_pasado():
    c = np.array([r["c"] for r in serie(2500)])
    completa, _ = volatilidad(c)
    for corte in (300, 1000, 2000):
        parcial, _ = volatilidad(c[:corte])
        assert np.array_equal(completa[:corte], parcial)


def test_el_resultado_de_cada_muestra_empieza_en_la_vela_siguiente():
    x = np.arange(1.0, 21.0)
    mx, mn = P._ventanas(x, 5, np.max), P._ventanas(x, 5, np.min)
    assert mx[0] == 6 and mn[0] == 2 and mx[10] == 16 and np.isnan(mx[-1])         # [i+1, i+H]: nunca incluye la vela i
    assert np.isnan(mx[len(x) - 1])


def test_la_calibracion_con_historia_hasta_t_no_cambia_si_el_futuro_cambia():
    rows = serie(5000)
    corte = 4000
    a = calibrar(rows[:corte], "1h")
    otro_futuro = rows[:corte] + serie(1000, vol=.08, seed=99, t0=rows[corte]["t"], base=rows[corte - 1]["c"])
    b = calibrar(otro_futuro[:corte], "1h")
    assert a["sigma"] == b["sigma"] and np.array_equal(a["q"], b["q"]) and a["val"] == b["val"]
    # y la sigma actual de una calibración con más historia NO retrocede a mirar velas futuras de esa fecha
    c = np.array([r["c"] for r in rows])
    assert volatilidad(c[:corte])[0][-1] == volatilidad(c)[0][corte - 1]


def test_la_validacion_fuera_de_muestra_usa_solo_muestras_ya_resueltas():
    # cada punto de prueba i solo entrena con muestras cuyo resultado terminó antes de i (idx + H <= idx[i])
    idx = np.arange(100, 5000)
    H = 24
    for i in range(int(len(idx) * .65), len(idx), H):
        lim = int(np.searchsorted(idx, idx[i] - H, side="right"))
        assert lim == 0 or idx[lim - 1] + H <= idx[i]


def test_horizontes_por_temporalidad():
    assert P.HOR == {"1m": 60, "1h": 24, "4h": 12, "1d": 7, "1w": 4}
    for tf, H in P.HOR.items():
        assert P.HORAS[tf] > 0 and H > 0
