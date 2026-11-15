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
    velas = [{"t": i * 60000, "c": 100 + i * .05} for i in range(200)]  # sube
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


# ---------- proyección por analogías (la flecha del gráfico) ----------
import json as _json  # noqa: E402

import numpy as _np  # noqa: E402

from cripto import fuentes as _f  # noqa: E402
from cripto.historico import agregar, empalmar  # noqa: E402
from cripto.proyeccion import HOR, QS, calibrar, compacto, proyectar, rasgos  # noqa: E402


def _raw(key, m):
    fx = _json.loads((FX / "mercado.json").read_text())
    return _f.parse_ohlc(fx["ohlc"][f"{key}|{m}"])[:-1]


def test_rasgos_sin_escala():
    """Los rasgos no cambian si el precio se multiplica por 1000 (sirven para empalmar mercados)."""
    raw = _raw("XXBTZEUR", 240)
    a = rasgos(raw, "4h")["F"]
    b = rasgos([dict(r, o=r["o"] * 1000, h=r["h"] * 1000, l=r["l"] * 1000, c=r["c"] * 1000) for r in raw], "4h")["F"]
    assert _np.allclose(a, b, atol=1e-6)


def test_calibracion_real_sin_futuro():
    raw = _raw("XXBTZEUR", 240)
    c = calibrar(raw, "4h")
    assert c and c["H"] == HOR["4h"] and c["muestras"] >= 150 and c["val"] and c["val"]["n"] >= 24
    # añadir velas futuras absurdas no cambia nada de lo calculado hasta la última vela conocida
    loca = raw + [dict(raw[-1], t=raw[-1]["t"] + 1, c=raw[-1]["c"] * 10)]
    assert calibrar(loca[:-1], "4h")["q"].tolist() == c["q"].tolist()
    # los cuantiles están ordenados y el recorrido acaba donde el resultado final
    assert all(c["q"][i] <= c["q"][i + 1] for i in range(4))
    assert _np.allclose(c["qp"][-1], c["q"])


def test_proyeccion_coherente():
    c = calibrar(_raw("XXBTZEUR", 1440), "1d")
    pr = proyectar(100.0, "1d", c, None, Z)
    assert pr["b80"][0] < pr["b50"][0] < pr["centro"] < pr["b50"][1] < pr["b80"][1]
    assert 0 < pr["p_up"] < 1 and len(pr["q"]["50"]) == HOR["1d"] and pr["q"]["50"][-1] == pr["centro"]
    assert all(len(a["ret"]) == HOR["1d"] for a in pr["analogos"])
    # los factores no técnicos inclinan la flecha, pero de forma acotada (±0,35 σ)
    neg = {"factores": [{"nombre": "Noticias 24 h", "aporte": -40}]}
    posi = {"factores": [{"nombre": "Noticias 24 h", "aporte": 40}]}
    a, b = proyectar(100.0, "1d", c, neg), proyectar(100.0, "1d", c, posi)
    assert a["centro"] < b["centro"] and b["p_up"] > a["p_up"]
    assert abs(_np.log(b["centro"] / a["centro"])) <= .7 * c["sigma"] * HOR["1d"] ** .5 + 1e-9
    # el pronóstico se ancla al precio actual: el centro relativo no depende de lo recorrido en la vela en curso
    alto = proyectar(c["c_ultima"] * 1.03, "1d", c)
    bajo = proyectar(c["c_ultima"] * .97, "1d", c)
    assert abs(alto["centro"] / (c["c_ultima"] * 1.03) - bajo["centro"] / (c["c_ultima"] * .97)) < 1e-9
    assert alto["recorrido"] > 0 > bajo["recorrido"]
    assert "q" not in compacto(pr) and compacto(pr)["dir"] == pr["dir"]


def test_analogias_solo_pesan_con_destreza():
    """Sin destreza fuera de muestra, la flecha no inventa dirección: la distribución base no tiene deriva."""
    c = calibrar(_raw("XXBTZEUR", 60), "1h")
    assert c["val"] is not None
    if c["val"]["destreza"] <= 0:
        assert c["lam"] == 0 and abs(c["q"][2]) < 1e-9 and c["p_up"] == .5
        assert proyectar(c["c_ultima"], "1h", c)["dir"] == "lateral"
    else:
        assert 0 < c["lam"] <= 1


def test_cobertura_validada():
    """La validación mide la cobertura real del cono; tras el ajuste conformal queda cerca de lo prometido."""
    c = calibrar(_raw("XXBTZEUR", 1440), "1d")
    v = c["val"]
    assert .3 <= v["cob50"] <= .7 and .6 <= v["cob80"] <= .95


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
