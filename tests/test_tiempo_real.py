"""Pruebas del flujo en vivo, el analizador fundamental y el sentimiento, con datos reales capturados."""
import json
import pathlib

from cripto.envivo import Libro, book_checksum, iso_ms
from cripto.noticias import analizar, agrupar, parse_feed, reaccion
from cripto.sentimiento import compuesto, lectura_derivados
from cripto.servicio import vela_viva

FX = pathlib.Path(__file__).parent / "fixtures"


# ---------- libro de órdenes: el CRC32 propio debe coincidir con el que envía Kraken ----------
def test_checksum_libro_kraken_real():
    d = json.loads((FX / "libro_ws.json").read_text())
    libros = {s: Libro(*p) for s, p in d["prec"].items()}
    n = 0
    for m in d["msgs"]:
        for x in m["data"]:
            assert libros[x["symbol"]].apply(x, m["type"] == "snapshot"), f"checksum distinto en {x['symbol']}"
            n += 1
    assert n >= 100
    assert sum(b.ok for b in libros.values()) >= 100 and all(b.bad == 0 for b in libros.values())


def test_checksum_detecta_corrupcion():
    d = json.loads((FX / "libro_ws.json").read_text())
    snap = next(m for m in d["msgs"] if m["type"] == "snapshot")["data"][0]
    lb = Libro(*d["prec"][snap["symbol"]])
    bad = json.loads(json.dumps(snap))
    bad["bids"][0]["qty"] += 0.001
    assert not lb.apply(bad, True)
    assert lb.bad == 1


def test_formato_checksum_documentado():
    # ejemplo de la documentación de Kraken: precio 0.05005 / cantidad 0.00000500 → "50055" + "500"
    a = [(0.05005, 0.000005)]
    assert book_checksum(a, [], 5, 8) == book_checksum([(0.05005, 0.000005)], [], 5, 8)
    import zlib
    assert book_checksum(a, [], 5, 8) == zlib.crc32(b"5005500") & 0xFFFFFFFF


def test_iso_ms():
    assert iso_ms("2026-11-15T03:05:28.318738902Z") == 1794711928318


# ---------- vela en formación ----------
H = 3600_000
RAW = [{"t": i * H, "o": 100, "h": 101, "l": 99, "c": 100, "v": 5} for i in range(10)]


def test_vela_viva_mismo_periodo():
    r = vela_viva(RAW, H, 103.5, 9 * H + 1000)
    assert len(r) == 10 and r[-1]["c"] == 103.5 and r[-1]["h"] == 103.5 and r[-1]["l"] == 99
    assert RAW[-1]["c"] == 100, "no debe modificar las velas originales"


def test_vela_viva_periodo_nuevo():
    r = vela_viva(RAW, H, 97, 12 * H + 5)
    assert [x["t"] for x in r[-3:]] == [10 * H, 11 * H, 12 * H]
    assert r[-1]["o"] == 100 and r[-1]["c"] == 97 and r[-1]["l"] == 97 and r[-1]["v"] == 0


# ---------- noticias ----------
def n(t, d=""):
    return {"titulo": t, "desc": d, "t": 0, "fuente": "x"}


def test_etf_salidas_negativo_btc():
    a = analizar(n("Bitcoin falls below $64,000 as ETF outflows extend to seventh straight day"))
    assert a["afecta"] == {"BTC": a["dir"]} and a["dir"] < 0 and "ETF e institucionales" in a["temas"]


def test_fed_recorta_positivo_y_oro():
    a = analizar(n("Fed cuts interest rates by 25 basis points, signals more easing ahead"), "macro")
    assert a["dir"] > 0 and a["afecta"]["MERCADO"] > 0 and a["afecta"]["PAXG"] > 0


def test_guerra_negativo_cripto_positivo_oro():
    a = analizar(n("Missile attack escalates conflict between Israel and Iran"), "macro")
    assert a["afecta"]["MERCADO"] < 0 and a["afecta"]["PAXG"] > 0


def test_inflacion_caliente_negativa():
    a = analizar(n("US CPI inflation comes in hotter than expected"), "macro")
    assert a["dir"] < 0


def test_hack_negativo():
    a = analizar(n("DeFi protocol drained of $40 million in exploit"))
    assert a["dir"] < 0 and "Seguridad" in a["temas"]


def test_negacion():
    a = analizar(n("SEC will not approve the new crypto ETF"))
    b = analizar(n("SEC approves the new crypto ETF"))
    assert b["dir"] > 0 and a["dir"] < b["dir"]


def test_bitcoin_cash_no_es_btc():
    a = analizar(n("Bitcoin Cash BCH cae con fuerza"))
    assert "BTC" not in a["monedas"]


def test_irrelevante_sin_efecto():
    a = analizar(n("SpaceX successfully catches Starship rocket"), "macro")
    assert a["afecta"] == {} and a["fuerza"] == 0


def test_parse_rss_real():
    items = parse_feed((FX / "rss_cointelegraph.xml").read_bytes(), "Cointelegraph")
    assert len(items) >= 10 and all(i["titulo"] and i["t"] > 1.7e12 for i in items)
    fed = parse_feed((FX / "rss_fed.xml").read_bytes(), "Reserva Federal")
    assert len(fed) >= 5


def test_agrupar_misma_historia():
    g = agrupar([dict(n("Bitcoin ETF outflows extend to seventh straight day"), fuente="A", t=0),
                 dict(n("Bitcoin ETF outflows extend to a seventh straight day as price falls"), fuente="B", t=60000),
                 dict(n("Solana launches new upgrade"), fuente="C", t=0)])
    assert len(g) == 2 and max(len(x["fuentes"]) for x in g) == 2


def test_reaccion_confirmada_y_contradicha():
    velas = [{"t": i * 60000, "o": 100 + i * .05, "c": 100 + i * .05} for i in range(200)]  # sube
    pos = {"t": 0, "dir": .8}
    neg = {"t": 0, "dir": -.8}
    assert reaccion(pos, velas, None, 200 * 60000)["conf"] == "Confirmada"
    assert reaccion(neg, velas, None, 200 * 60000)["conf"] == "Contradicha"


# ---------- sentimiento ----------
def test_compuesto_rango_y_lectura():
    s = compuesto(fg=10, mc24=-7, amplitud=0, funding_med=-.0001, largos_med=35, pulso=-60)
    assert 0 <= s["valor"] < 20 and s["lectura"] == "Miedo extremo"
    s = compuesto(fg=90, mc24=7, amplitud=100, funding_med=.0004, largos_med=85, pulso=80)
    assert s["valor"] > 80 and s["lectura"] == "Codicia extrema"
    assert abs(sum(c["peso"] for c in s["componentes"]) - 1) < 1e-9


def test_lectura_derivados():
    x = lectura_derivados({"funding": .0004, "largos_pct": 80, "oi_24h": 5}, -2)
    txt = " ".join(t for _, t in x)
    assert "Funding muy alto" in txt and "señal contraria" in txt and "entran cortos" in txt


# ---------- previsión combinada ----------
from cripto.noticias import efecto, resumen_impacto  # noqa: E402
from cripto.prevision import prevision  # noqa: E402

Z = {"sup": [{"lo": 95, "hi": 97, "mid": 96, "s": 3}], "res": [{"lo": 104, "hi": 106, "mid": 105, "s": 3}]}


def moneda(ts, tl, tm, micro):
    return {"price": 100, "z": Z, "ts": ts, "tl": tl, "tm": {"v": tm}, "micro": {"score": micro, "lectura": "x"}, "d1": {"atr": 2}}


def test_prevision_bajista_objetivo_e_invalidacion():
    p = prevision(moneda(20, 25, "V", -60), pulso={"score": -50, "lectura": "Negativo", "n": 5})
    assert p["dir"] == "bajista" and p["objetivo"]["precio"] == 96 and p["invalidacion"]["precio"] == 106
    assert abs(sum(f["aporte"] for f in p["factores"]) - p["score"]) < 1e-6


def test_prevision_alcista_y_lateral():
    a = prevision(moneda(80, 75, "C", 60))
    assert a["dir"] == "alcista" and a["objetivo"]["precio"] == 105 and a["invalidacion"]["precio"] == 93
    n = prevision(moneda(50, 50, "E", 0))
    assert n["dir"] == "lateral" and n["objetivo"]["rango"] == [96, 105] and n["conf"] == "baja"


def test_derivados_y_sentimiento_son_contrarios():
    base = moneda(50, 50, "E", 0)
    euforia = prevision(base, deriv={"ok": True, "funding": .0004, "largos_pct": 85}, sent={"valor": 90, "lectura": "Codicia extrema"})
    miedo = prevision(base, deriv={"ok": True, "funding": -.0002, "largos_pct": 35}, sent={"valor": 10, "lectura": "Miedo extremo"})
    assert euforia["score"] < 0 < miedo["score"]


def test_efecto_y_resumen_noticias():
    n1 = {"t": 0, "afecta": {"BTC": -1.0}, "dir": -1.0, "fuerza": 7, "temas": ["ETF e institucionales"], "horiz": "días", "reac": {"conf": "Confirmada"}}
    n2 = {"t": 0, "afecta": {"MERCADO": -.5}, "dir": -.5, "fuerza": 4, "temas": ["Regulación"], "horiz": "días a semanas", "reac": None}
    assert efecto(n1).startswith("Presión bajista fuerte sobre BTC")
    r = resumen_impacto([n1, n2], "BTC", {"BTC": {"score": -60}}, ahora=1)
    assert r["dir"] == "bajista" and r["n"] == 2 and r["confirmadas"] == 1 and "Presión bajista" in r["efecto"]
    assert resumen_impacto([], "SOL", {})["n"] == 0


# ---------- proyección por volatilidad: rango y probabilidad de tocar niveles ----------
import json as _json  # noqa: E402

import numpy as _np  # noqa: E402

from cripto import fuentes as _f  # noqa: E402
from cripto.historico import agregar, empalmar  # noqa: E402
from cripto.proyeccion import HOR, calibrar, compacto, p_tocar, proyectar, volatilidad  # noqa: E402


def _raw(key, m):
    fx = _json.loads((FX / "mercado.json").read_text())
    return _f.parse_ohlc(fx["ohlc"][f"{key}|{m}"])[:-1]


def _sintetica(n=6000, seed=3, cambio=250, vols=(.004, .02)):
    """Serie con rachas de volatilidad (como los mercados reales) y sin ninguna deriva: la respuesta correcta se conoce."""
    rng = _np.random.default_rng(seed)
    sig = _np.repeat(rng.choice(vols, n // cambio + 1), cambio)[:n]
    c = 100 * _np.exp(_np.cumsum(rng.standard_normal(n) * sig))
    o = _np.concatenate([[100.0], c[:-1]])
    return [{"t": i * 14400000, "o": float(o[i]), "h": float(max(o[i], c[i]) * (1 + abs(rng.standard_normal()) * sig[i] * .4)),
             "l": float(min(o[i], c[i]) * (1 - abs(rng.standard_normal()) * sig[i] * .4)), "c": float(c[i]), "v": 1.0} for i in range(n)]


def test_volatilidad_no_depende_de_la_escala():
    raw = _raw("XXBTZEUR", 240)
    a, _ = volatilidad(_np.array([r["c"] for r in raw]))
    b, _ = volatilidad(_np.array([r["c"] * 1000 for r in raw]))
    assert _np.allclose(a, b, atol=1e-12) and (a > 0).all()


def test_calibracion_real_sin_futuro():
    raw = _raw("XXBTZEUR", 240)
    c = calibrar(raw, "4h")
    assert c and c["H"] == HOR["4h"] and c["muestras"] >= 150 and c["val"] and c["val"]["n"] >= 12
    # añadir velas futuras absurdas no cambia nada de lo calculado hasta la última vela conocida
    loca = raw + [dict(raw[-1], t=raw[-1]["t"] + 1, c=raw[-1]["c"] * 10)]
    assert calibrar(loca[:-1], "4h")["q"].tolist() == c["q"].tolist()
    # cuantiles ordenados, sin deriva (mediana cero) y el recorrido acaba en el cuantil final
    assert all(c["q"][i] <= c["q"][i + 1] for i in range(4)) and abs(c["q"][2]) < 1e-12
    assert _np.allclose(c["qp"][-1], c["q"])
    # las probabilidades de llegar a un nivel bajan al alejarse el nivel
    assert (_np.diff(c["F_up"]) <= 1e-12).all() and (_np.diff(c["F_dn"]) <= 1e-12).all() and c["F_up"][0] == 1.0


def test_proyeccion_coherente_y_sin_direccion():
    c = calibrar(_raw("XXBTZEUR", 1440), "1d")
    z = {"res": [{"lo": 101.0, "hi": 101.5, "mid": 101.2}, {"lo": 105.0, "hi": 106.0, "mid": 105.5}],
         "sup": [{"lo": 98.0, "hi": 99.0, "mid": 98.5}, {"lo": 93.0, "hi": 94.0, "mid": 93.5}]}
    pr = proyectar(100.0, "1d", c, None, z)
    assert pr["b80"][0] < pr["b50"][0] < pr["centro"] < pr["b50"][1] < pr["b80"][1] and pr["centro"] == 100.0
    assert len(pr["q"]["50"]) == HOR["1d"] and pr["q"]["50"][-1] == pr["centro"] and pr["dir"] == "lateral"
    # más lejos, menos probable (en ambos lados), y todo en [0, 1]
    p = {g["id"]: g["p"] for g in pr["niveles"]}
    assert p["S1"] > p["S2"] and p["E1"] > p["E2"] and all(0 <= v <= 1 for v in p.values())
    # un nivel que el precio ya ha alcanzado tiene probabilidad 1
    assert p_tocar(c, 102.0, 101.0, "sube") == 1.0 and p_tocar(c, 100.0, 101.0, "baja") == 1.0
    # las noticias, el sentimiento y demás factores NO inclinan el rango: no tienen ventaja demostrada
    neg = {"factores": [{"nombre": "Noticias 24 h", "aporte": -40}]}
    posi = {"factores": [{"nombre": "Noticias 24 h", "aporte": 40}]}
    a, b = proyectar(100.0, "1d", c, neg), proyectar(100.0, "1d", c, posi)
    assert a["b50"] == b["b50"] and a["centro"] == b["centro"]
    # el rango es proporcional al precio (se ancla al precio actual)
    alto = proyectar(c["c_ultima"] * 1.03, "1d", c)
    assert abs(alto["b50"][1] / (c["c_ultima"] * 1.03) - pr["b50"][1] / 100.0) < 1e-9
    assert "q" not in compacto(pr) and compacto(pr)["niveles"] == pr["niveles"]


def test_cobertura_validada():
    """La validación mide la cobertura real del rango (50 % y 80 %) con datos que el método no había visto."""
    c = calibrar(_raw("XXBTZEUR", 1440), "1d")
    v = c["val"]
    assert .3 <= v["cob50"] <= .7 and .6 <= v["cob80"] <= .95


def test_sintetica_con_rachas_de_volatilidad():
    """Con volatilidad que agrupa y sin deriva, el rango cubre lo prometido y la volatilidad actual mejora las probabilidades."""
    c = calibrar(_sintetica(), "4h")
    v = c["val"]
    assert .4 <= v["cob50"] <= .6 and .7 <= v["cob80"] <= .9, v
    assert v["brier_mejora"] > .03, v


def test_sintetica_con_volatilidad_constante_no_inventa_ventaja():
    """Sin rachas de volatilidad no hay nada que aprender: la mejora de las probabilidades es nula (no se inventa ventaja)."""
    c = calibrar(_sintetica(vols=(.01, .01)), "4h")
    assert abs(c["val"]["brier_mejora"]) < .03, c["val"]


def test_casos_son_reales_representativos_y_sin_futuro():
    rows = _sintetica()
    c = calibrar(rows, "4h")
    cs = c["casos"]
    assert cs and cs["k"] == 50 and len(cs["rep"]) == 5 and "nube" not in cs
    # sin futuro: cada caso, con todo su recorrido, terminó antes de la última vela conocida
    assert (cs["t"] + HOR["4h"] * 14400000 <= rows[-1]["t"]).all()
    # los 5 están ordenados por resultado y no se repiten
    fin = cs["y"][cs["rep"]]
    assert (_np.diff(fin) >= 0).all() and len(set(cs["rep"])) == 5
    # representativos: su dispersión se parece a la de los 50 (los 4 más parecidos la subestiman)
    assert .8 < fin.std() / cs["y"].std() < 1.3
    pr = proyectar(rows[-1]["c"], "4h", c)
    assert len(pr["analogos"]) == 5 and [a["rep"] for a in pr["analogos"]] == [1, 2, 3, 4, 5]
    assert all(len(a["ret"]) == HOR["4h"] for a in pr["analogos"])
    assert pr["casos"]["sube"] + pr["casos"]["baja"] == 50
    assert "analogos" not in compacto(pr) and "q" not in compacto(pr)


def test_casos_validados_fuera_de_muestra():
    """Con volatilidad que agrupa, la envolvente de los casos reproduce la dispersión real (no se queda corta)."""
    v = calibrar(_sintetica(), "4h")["val_casos"]
    assert v["n"] >= 12 and .35 <= v["env50"] <= .65 and .65 <= v["env80"] <= .95 and .8 <= v["razon"] <= 1.25, v


def test_historico_demasiado_corto_no_calibra():
    assert calibrar(_sintetica(n=150), "4h") is None


def test_empalme_ajusta_escala():
    kr = _raw("XXBTZEUR", 60)
    # "otro mercado" cotizado al doble, que cubre antes y durante las velas de Kraken
    otro = [dict(r, t=r["t"] - 100 * 3600000, o=r["o"] * 2, h=r["h"] * 2, l=r["l"] * 2, c=r["c"] * 2) for r in kr]
    s = empalmar(otro, kr)
    assert len(s) == len(kr) + 100 and all(s[i]["t"] < s[i + 1]["t"] for i in range(len(s) - 1))
    assert abs(s[99]["c"] / kr[0]["o"] - 1) < .05, "la escala se ajusta en la unión"
    assert s[100:] == kr
    assert empalmar([], kr) == kr and empalmar(otro, []) == otro


def test_agregar_velas():
    rows = [{"t": i * 3600000, "o": i, "h": i + 2, "l": i - 1, "c": i + 1, "v": 1} for i in range(8)]
    g = agregar(rows, 4)
    assert len(g) == 2 and g[0]["o"] == 0 and g[0]["c"] == 4 and g[0]["h"] == 5 and g[0]["l"] == -1 and g[0]["v"] == 4


def test_velas_1m_sin_huecos():
    from cripto.envivo import Flujo
    v = {0: {"t": 0, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}, 180000: {"t": 180000, "o": 2, "h": 2, "l": 2, "c": 2, "v": 1}}
    r = Flujo._rellenar(v)
    assert [x["t"] for x in r] == [0, 60000, 120000, 180000] and r[1]["c"] == 1 and r[1]["v"] == 0
