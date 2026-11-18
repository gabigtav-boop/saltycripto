"""Noticias: clasificación, relevancia, duplicados, antigüedad, fechas y el pulso (auditoría 17 nov 2026)."""
import math
from datetime import datetime, timezone

import pytest

from cripto import noticias as N
from cripto.noticias import Fundamental, agrupar, analizar, clase_lectura, parse_date, parse_feed

H = 36e5


def an(titulo, tipo="cripto", desc=""):
    return analizar({"titulo": titulo, "desc": desc}, tipo, 1.0)


# ---------- positivas, negativas, neutrales, mixtas, información insuficiente ----------
def test_noticia_positiva_y_negativa():
    p = an("Fed cuts rates; Bitcoin surges", "macro")
    n = an("Bitcoin ETF outflows hit record as institutions sell")
    assert p["lectura"] == "Positiva" and p["clase"] == "positiva" and p["dir"] > .5 and "BTC" in p["afecta"]
    assert n["lectura"] == "Negativa" and n["clase"] == "negativa" and n["dir"] < -.5 and n["afecta"]["BTC"] < 0
    assert n["motivos"], "cada lectura debe poder explicarse con las palabras que la activaron"


def test_la_negacion_invierte_la_lectura():
    assert an("Fed does not cut rates, Powell hawkish", "macro")["clase"] == "negativa"
    assert an("Fed will not hike rates, dovish tone", "macro")["clase"] == "positiva"


def test_sin_palabras_de_lectura_es_informacion_insuficiente_no_neutral():
    x = an("Bitcoin conference to be held in Miami next month")
    assert x["clase"] == "insuficiente" and x["dir"] == 0 and x["lectura"] == "Neutral"      # `lectura` no cambia: la UI la usa
    assert "BTC" in x["monedas"]


def test_noticia_mixta():
    assert clase_lectura(1.0, 1.0, 0.0) == "mixta"
    assert clase_lectura(2.0, 1.0, 1 / 3) == "mixta"           # el 33 % del peso va en contra: no es una lectura limpia
    assert clase_lectura(5.0, 0.2, .92) == "positiva"
    assert clase_lectura(0.0, 3.0, -1.0) == "negativa"
    assert clase_lectura(0.0, 0.0, 0.0) == "insuficiente"
    assert an("Hack drains $50M from DeFi protocol, funds recovered")["clase"] == "mixta"


def test_la_titular_de_precio_se_atenua():
    asunto = an("Bitcoin price prediction: BTC could surge to $200K")
    real = an("Bitcoin ETF inflows surge as BlackRock buys")
    assert asunto["fuerza"] < real["fuerza"] and asunto["fuerza"] < 1.5


# ---------- relevancia y asignación a activos ----------
def test_otras_divisas_no_se_leen_como_el_dolar():
    aud = an("Australian Dollar steadies at a 10-month low after split RBA minutes", "macro")
    usd = an("Dollar index falls to a 10-month low", "macro")
    assert "Dólar y bonos" not in aud["temas"] and aud["fuerza"] == 0 and aud["afecta"] == {}
    assert "Dólar y bonos" in usd["temas"] and usd["afecta"]
    assert "Dólar y bonos" not in an("Canadian dollar and loonie slide", "macro")["temas"]


def test_noticia_que_afecta_a_varios_activos():
    x = an("Bitcoin and Ethereum surge as Solana hits record high")
    assert {"BTC", "ETH", "SOL"} <= set(x["afecta"]) and all(v > 0 for v in x["afecta"].values())


def test_macro_afecta_al_mercado_y_al_oro_con_signo_propio():
    x = an("Fed signals rate cuts as inflation cools", "macro")
    assert "MERCADO" in x["afecta"] and "PAXG" in x["afecta"]


def test_palabras_ambiguas_no_asignan_monedas():
    assert an("Sol Pharmaceuticals shares drop", "macro")["monedas"] == []
    assert "SOL" not in an("The sol was bright and hype was everywhere", "macro")["monedas"]
    assert "BTC" not in an("Bitcoin Cash upgrade planned")["monedas"]


# ---------- duplicados ----------
def _it(t, fuente, titulo):
    return {"titulo": titulo, "t": t, "fuente": fuente, "link": "", "desc": ""}


def test_la_misma_historia_de_varios_medios_se_agrupa():
    g = agrupar([_it(1e12, "CoinDesk", "BlackRock bitcoin ETF sees record inflows as institutions buy"),
                 _it(1e12 + 600e3, "Decrypt", "Bitcoin ETF from BlackRock sees record inflows as institutions buy"),
                 _it(1e12 + 700e3, "The Block", "Ethereum developers delay scaling upgrade to next quarter")])
    assert len(g) == 2 and sorted(max(g, key=lambda x: len(x["fuentes"]))["fuentes"]) == ["CoinDesk", "Decrypt"]


def test_misma_historia_con_doce_horas_de_diferencia_no_se_funde():
    g = agrupar([_it(1e12, "A", "Bitcoin ETF sees record inflows from BlackRock"), _it(1e12 + 13 * H, "B", "Bitcoin ETF sees record inflows from BlackRock")])
    assert len(g) == 2


# ---------- antigüedad y fechas ----------
def _noticia(t, d, fuerza=5.0, clase="positiva"):
    return {"t": t, "fuerza": fuerza, "afecta": {"BTC": d}, "clase": clase}


def test_noticias_antiguas_pesan_menos_con_vida_media_de_8_horas():
    ahora = 100 * H
    reciente_pos, vieja_neg = _noticia(ahora - 1 * H, 1.0), _noticia(ahora - 24 * H, -1.0)
    assert Fundamental.pulso([reciente_pos, vieja_neg], ahora)["BTC"]["score"] > 50
    # misma fuerza (12): la de hace 8 h pesa la mitad (6) frente a la de ahora (12) → (12 - 6) / (12 + 6) = +33,3
    mezcla = Fundamental.pulso([_noticia(ahora, 1.0, 12), _noticia(ahora - 8 * H, -1.0, 12)], ahora)["BTC"]["score"]
    assert mezcla == pytest.approx(100 / 3, rel=1e-6)


def test_pulso_conserva_su_aritmetica_y_anade_n_lectura():
    ahora = 100 * H
    lst = [_noticia(ahora, 1.0, 5), _noticia(ahora, -1.0, 3), _noticia(ahora, 0.0, 4, "insuficiente")]
    p = Fundamental.pulso(lst, ahora)["BTC"]
    assert p["score"] == pytest.approx((5 - 3) / max(12, 6) * 100) and p["n"] == 3 and p["n_lectura"] == 2
    assert Fundamental.pulso([_noticia(ahora, 0.0, 4, "insuficiente")], ahora)["BTC"]["n_lectura"] == 0
    sin_clase = {"t": ahora, "fuerza": 5, "afecta": {"BTC": 1.0}}                      # datos antiguos sin la clave
    assert Fundamental.pulso([sin_clase], ahora)["BTC"]["n_lectura"] == 1


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Bitcoin surges</title><link>http://x/1</link><pubDate>Mon, 17 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Sin fecha alguna</title><link>http://x/2</link></item>
<item><title>Fecha rota</title><pubDate>ayer por la tarde</pubDate></item>
<item><title>Naive sin zona</title><pubDate>2026-11-17T10:00:00</pubDate></item></channel></rss>"""


def test_noticias_sin_fecha_o_con_fecha_ilegible_no_entran():
    its = parse_feed(RSS, "Prueba")
    assert [i["titulo"] for i in its] == ["Bitcoin surges", "Naive sin zona"]


def test_fecha_sin_zona_se_lee_como_utc():
    esperado = int(datetime(2026, 11, 17, 10, tzinfo=timezone.utc).timestamp() * 1000)
    assert parse_date("2026-11-17T10:00:00") == esperado == parse_date("Mon, 17 Nov 2026 10:00:00 -0000") == parse_date("2026-11-17T10:00:00Z")
    assert parse_date("") is None and parse_date(None) is None and parse_date("no es fecha") is None


def test_el_precio_no_se_inventa_si_no_hay_velas_antes_de_la_noticia():
    assert N.precio_en([{"t": 10, "c": 1.0}], 5) is None and N.precio_en([], 5) is None
    assert N.reaccion({"t": 5, "dir": 1}, [{"t": 10, "c": 1.0}], [], 1e6) is None


def test_reaccion_no_prueba_causalidad_y_distingue_sin_reaccion():
    velas = [{"t": i * 6e4, "o": 100.0, "c": 100.0 + (0.01 if i < 70 else 0)} for i in range(200)]
    r = N.reaccion({"t": 0, "dir": 1.0}, velas, [], 199 * 6e4)
    assert r["conf"] == "Sin reacción" and math.isfinite(r["ahora"])
