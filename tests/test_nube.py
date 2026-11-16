"""Pruebas de la versión nube: cadena de fuentes de derivados, honestidad de la flecha, noticias y parte de la web.
Todo sin red: las fuentes se sustituyen por respuestas simuladas con el formato real de cada servidor."""
import pathlib
import re
import shutil
import subprocess

import pytest

from cripto import sentimiento
from cripto.noticias import analizar
from cripto.proyeccion import direccion_valida

RAIZ = pathlib.Path(__file__).resolve().parent.parent
HTML = (RAIZ / "web" / "index.html").read_text()


# ---------- derivados: Binance → Bybit → OKX → Kraken Futures ----------
def _red(monkeypatch, respuestas):
    """Sustituye fuentes.jget: la URL se compara por fragmentos; una excepción simula un bloqueo (403)."""
    def jget(url, timeout=12):
        for frag, r in respuestas.items():
            if frag in url:
                if isinstance(r, Exception):
                    raise r
                return r
        raise RuntimeError("sin respuesta simulada para " + url)
    monkeypatch.setattr(sentimiento.fuentes, "jget", jget)
    monkeypatch.setattr(sentimiento.time, "sleep", lambda *_: None)


BYBIT = {
    "bybit.com/v5/market/tickers": {"result": {"list": [{"fundingRate": "0.0001", "fundingIntervalHour": "8", "markPrice": "100",
                                                       "openInterestValue": "5000"}]}},
    "bybit.com/v5/market/account-ratio": {"result": {"list": [{"buyRatio": "0.7", "sellRatio": "0.3"}]}},
    "bybit.com/v5/market/open-interest": {"result": {"list": [{"openInterest": "110"}, {"openInterest": "100"}]}},
}
OKX = {
    "okx.com/api/v5/public/funding-rate": {"code": "0", "data": [{"fundingRate": "0.0001", "fundingTime": "1000000000000", "nextFundingTime": "1000014400000"}]},
    "long-short-account-ratio": {"code": "0", "data": [["1", "2.5"]]},
    "open-interest-volume": {"code": "0", "data": [["2", "120", "0"]] + [["1", "100", "0"]] * 24},
}
KRAKEN = {"futures.kraken.com": {"tickers": [{"symbol": "PF_XBTUSD", "relativeFundingRate": "0.00001", "openInterest": "10", "markPrice": "50"}]}}


def test_binance_bloqueado_usa_bybit(monkeypatch):
    monkeypatch.setattr(sentimiento, "SIN_BINANCE", True)
    _red(monkeypatch, {**BYBIT, **KRAKEN})
    d = sentimiento.derivados(["BTC"])["BTC"]
    assert d["ok"] and d["fuente"] == "bybit"
    assert d["largos_pct"] == pytest.approx(70) and d["oi_24h"] == pytest.approx(10)
    assert d["funding"] == pytest.approx(1e-4)


def test_bybit_bloqueado_usa_okx_y_normaliza_el_funding_a_8h(monkeypatch):
    monkeypatch.setattr(sentimiento, "SIN_BINANCE", True)
    _red(monkeypatch, {"bybit.com": RuntimeError("403 Forbidden"), **OKX, **KRAKEN})
    d = sentimiento.derivados(["BTC"])["BTC"]
    assert d["ok"] and d["fuente"] == "okx"
    assert d["largos_pct"] == pytest.approx(2.5 / 3.5 * 100)       # ratio largos/cortos → % de cuentas en largo
    assert d["funding"] == pytest.approx(2e-4)                     # 0,01 % cada 4 h = 0,02 % cada 8 h
    assert d["oi_24h"] == pytest.approx(20)


def test_sin_ninguna_fuente_queda_kraken_futures_sin_cuentas_en_largo(monkeypatch):
    monkeypatch.setattr(sentimiento, "SIN_BINANCE", True)
    _red(monkeypatch, {"bybit.com": RuntimeError("403"), "okx.com": RuntimeError("429"), **KRAKEN})
    d = sentimiento.derivados(["BTC"])["BTC"]
    assert d["ok"] and d["fuente"] == "kraken"
    assert d["largos_pct"] is None and d["oi_24h"] is None
    assert d["funding"] == pytest.approx(8e-5)                     # por hora × 8


def test_a_8h():
    assert sentimiento.a_8h(1e-4, 8) == pytest.approx(1e-4)
    assert sentimiento.a_8h(1e-5, 1) == pytest.approx(8e-5)
    assert sentimiento.a_8h(1e-4, None) == 1e-4 and sentimiento.a_8h(None, 8) is None


# ---------- flecha: la dirección solo cuenta si la validación la respalda ----------
def test_direccion_valida():
    assert direccion_valida(None)                                   # sin validación: decide la destreza
    assert direccion_valida({"llamadas": 5, "acierto": .2})          # pocas llamadas: no se descarta
    assert direccion_valida({"llamadas": 60, "acierto": .55})
    assert not direccion_valida({"llamadas": 60, "acierto": .42})    # peor que lanzar una moneda


# ---------- noticias: tickers que son palabras corrientes ----------
@pytest.mark.parametrize("titulo,esperado", [
    ("Solana (SOL) jumps 8% as traders pile in", ["SOL"]),
    ("The sol shines on Madrid beaches today", []),
    ("Market hype builds ahead of the ETF decision", []),
    ("HYPE token hits an all-time high on Hyperliquid", ["HYPE"]),
    ("Sui network upgrade goes live", ["SUI"]),
    ("sui generis legal case reaches court", []),
])
def test_tickers_de_palabras_corrientes(titulo, esperado):
    assert analizar({"titulo": titulo, "desc": ""}, "cripto", 1.0)["monedas"] == esperado


# ---------- web: lectura de números en formato español (calculadora de posición) ----------
@pytest.mark.skipif(shutil.which("node") is None, reason="necesita Node.js")
def test_calculadora_entiende_numeros_en_formato_espanol():
    m = re.search(r"const num=x=>\{.*?return isFinite\(v\)&&v>0\?v:0\}", HTML)
    assert m, "no se encontró num() en la web"
    casos = {"3.200": 3200, "1.500,25": 1500.25, "1500.25": 1500.25, "0,0031": .0031, "0.05": .05, "1.5": 1.5,
             "58.965": 58965, "1.234.567": 1234567, "abc": 0, "-3": 0, "": 0}
    js = m.group(0) + ";console.log(JSON.stringify(" + str(list(casos)).replace("'", '"') + ".map(num)))"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert [round(x, 6) for x in __import__("json").loads(out)] == [round(v, 6) for v in casos.values()]


def test_web_tiene_las_protecciones_de_iphone():
    assert "font-size:16px" in HTML                                 # sin zoom al escribir en los campos
    assert "overflow-x:auto" in HTML and "touch-action:pan-x" in HTML  # tira de casos deslizable
    assert "function morph" in HTML                                  # repintado que conserva el desplazamiento
    assert "popstate" in HTML                                        # gesto de atrás
