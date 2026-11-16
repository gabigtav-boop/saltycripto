"""Sentimiento del mercado: derivados (funding, interés abierto, posicionamiento largo/corto),
miedo y codicia, amplitud técnica, impulso y pulso de noticias, combinados en un índice 0-100
con todos sus componentes visibles."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import fuentes

BINANCE = "https://fapi.binance.com"
BYBIT = "https://api.bybit.com"
OKX = "https://www.okx.com"
SIN_BINANCE = os.environ.get("SALTY_SIN_BINANCE") == "1"   # para probar en local cómo funciona sin Binance
KFUT = "https://futures.kraken.com/derivatives/api/v3/tickers"
KF_SYM = {"BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "XRP": "PF_XRPUSD", "SOL": "PF_SOLUSD", "HYPE": "PF_HYPEUSD",
          "SUI": "PF_SUIUSD", "ZEC": "PF_ZECUSD", "PAXG": "PF_PAXGUSD"}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


_INTERVALOS = {"t": 0, "d": {}}


def intervalos_funding():
    """Horas entre cobros de funding de los contratos de Binance que no usan las 8 h habituales (cache de 1 h)."""
    if time.time() - _INTERVALOS["t"] > 3600:
        try:
            _INTERVALOS["d"] = {x["symbol"]: _f(x.get("fundingIntervalHours")) for x in fuentes.jget(f"{BINANCE}/fapi/v1/fundingInfo", 8)}
            _INTERVALOS["t"] = time.time()
        except Exception:  # noqa: BLE001
            _INTERVALOS["t"] = time.time() - 3000     # reintenta en unos minutos
    return _INTERVALOS["d"]


def binance_moneda(s):
    sym = s + "USDT"
    out = {"ok": False}
    if SIN_BINANCE:
        out["error"] = "Binance no disponible desde este servidor"
        return s, out
    try:
        p = fuentes.jget(f"{BINANCE}/fapi/v1/premiumIndex?symbol={sym}", 8)
        out["funding"] = a_8h(_f(p.get("lastFundingRate")), intervalos_funding().get(sym))   # equivalente a 8 h
        out["mark"] = _f(p.get("markPrice"))
        out["next_funding"] = p.get("nextFundingTime")
        ls = fuentes.jget(f"{BINANCE}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=1", 8)
        if ls:
            out["largos_pct"] = _f(ls[-1]["longAccount"]) * 100
            out["ls_ratio"] = _f(ls[-1]["longShortRatio"])
        tp = fuentes.jget(f"{BINANCE}/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=1", 8)
        if tp:
            out["top_largos_pct"] = _f(tp[-1]["longAccount"]) * 100
        oi = fuentes.jget(f"{BINANCE}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=25", 8)
        if oi and len(oi) >= 2:
            a, b = _f(oi[0]["sumOpenInterestValue"]), _f(oi[-1]["sumOpenInterestValue"])
            out["oi_usd"] = b
            out["oi_24h"] = (b / a - 1) * 100 if a else None
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:80]
    return s, out


def a_8h(tasa, horas):
    """Funding por periodo → equivalente a 8 h (Binance cobra cada 8 h; otros contratos, cada 1, 2 o 4 h)."""
    if tasa is None:
        return None
    return tasa * 8 / horas if horas and 0 < horas <= 24 else tasa


def bybit_moneda(s):
    """Respaldo cuando Binance no responde (p. ej. desde los servidores de GitHub): Bybit publica funding,
    reparto de cuentas en largo e interés abierto (con histórico) para los mismos perpetuos USDT."""
    sym = s + "USDT"
    out = {"ok": False}
    try:
        t = fuentes.jget(f"{BYBIT}/v5/market/tickers?category=linear&symbol={sym}", 8)["result"]["list"][0]
        out["funding"] = a_8h(_f(t.get("fundingRate")), _f(t.get("fundingIntervalHour")))
        out["mark"] = _f(t.get("markPrice"))
        out["oi_usd"] = _f(t.get("openInterestValue"))
        try:
            ls = fuentes.jget(f"{BYBIT}/v5/market/account-ratio?category=linear&symbol={sym}&period=1h&limit=1", 8)["result"]["list"]
            if ls:
                out["largos_pct"] = _f(ls[0]["buyRatio"]) * 100
                out["ls_ratio"] = _f(ls[0]["buyRatio"]) / max(_f(ls[0]["sellRatio"]) or 1e-9, 1e-9)
        except Exception:  # noqa: BLE001
            pass
        try:
            oi = fuentes.jget(f"{BYBIT}/v5/market/open-interest?category=linear&symbol={sym}&intervalTime=1h&limit=25", 8)["result"]["list"]
            if oi and len(oi) >= 2:                              # Bybit devuelve del más reciente al más antiguo
                b, a = _f(oi[0]["openInterest"]), _f(oi[-1]["openInterest"])
                out["oi_24h"] = (b / a - 1) * 100 if a else None
        except Exception:  # noqa: BLE001
            pass
        out["ok"] = out["funding"] is not None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:80]
    return out


_okx_lock = threading.Lock()


def _okx_get(url):
    """OKX limita las peticiones por IP (las estadísticas, a unas pocas por segundo): se hacen de una en una,
    espaciadas, y con un reintento."""
    for i in range(2):
        with _okx_lock:
            try:
                d = fuentes.jget(url, 8)
            except Exception:  # noqa: BLE001
                d = None
            time.sleep(.3)
        if d and d.get("code") == "0":
            return d["data"]
        time.sleep(1.2)
    return None


def okx_moneda(s):
    """Segundo respaldo: OKX (reparto largo/corto e interés abierto por moneda; funding del perpetuo USDT)."""
    out = {"ok": False}
    try:
        f = _okx_get(f"{OKX}/api/v5/public/funding-rate?instId={s}-USDT-SWAP")
        if f:
            t0, t1 = _f(f[0].get("fundingTime")), _f(f[0].get("nextFundingTime"))
            out["funding"] = a_8h(_f(f[0].get("fundingRate")), (t1 - t0) / 3.6e6 if (t0 and t1 and t1 > t0) else None)
        try:
            ls = _okx_get(f"{OKX}/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy={s}&period=1H")
            r = _f(ls[0][1]) if ls else None
            if r is not None:
                out["largos_pct"] = r / (1 + r) * 100
                out["ls_ratio"] = r
        except Exception:  # noqa: BLE001
            pass
        try:
            oi = _okx_get(f"{OKX}/api/v5/rubik/stat/contracts/open-interest-volume?ccy={s}&period=1H")
            if oi and len(oi) >= 2:
                b = _f(oi[0][1])
                a = _f(oi[min(24, len(oi) - 1)][1])
                out["oi_usd"] = b
                out["oi_24h"] = (b / a - 1) * 100 if a else None
        except Exception:  # noqa: BLE001
            pass
        out["ok"] = out.get("funding") is not None
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:80]
    return out


def _moneda(s):
    """Derivados de una moneda probando las fuentes por orden: Binance → Bybit → OKX."""
    _, out = binance_moneda(s)
    if out.get("ok"):
        out["fuente"] = "binance"
        return s, out
    errs = [out.get("error")]
    for nombre, fn in (("bybit", bybit_moneda), ("okx", okx_moneda)):
        o = fn(s)
        if o.get("ok"):
            o["fuente"] = nombre
            o["error_binance"] = errs[0]
            return s, o
        errs.append(o.get("error"))
    return s, {"ok": False, "error": " · ".join(str(e) for e in errs if e)[:120]}


def derivados(monedas):
    """Datos de derivados por moneda (Binance, o Bybit/OKX si no responde) con contraste del funding en
    Kraken Futures; si ninguna responde, Kraken aporta funding e interés abierto (sin reparto de cuentas)."""
    with ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(_moneda, monedas))
    try:
        kt = {t["symbol"]: t for t in fuentes.jget(KFUT, 10)["tickers"]}
        for s in monedas:
            t = kt.get(KF_SYM.get(s))
            if t:
                res[s]["kraken_funding_h"] = _f(t.get("relativeFundingRate"))   # por hora
                res[s]["kraken_oi"] = _f(t.get("openInterest"))
                if res[s].get("ok"):
                    if res[s].get("funding") is not None and res[s]["kraken_funding_h"] is not None:
                        a, b = res[s]["funding"], res[s]["kraken_funding_h"]
                        res[s]["funding_coincide"] = (a >= 0) == (b >= 0) or abs(a) < 2e-5 or abs(b) < 2e-6
                else:
                    # Último respaldo: Kraken Futures. Su funding es por hora; ×8 lo hace comparable con el de
                    # Binance (cada 8 h). Kraken no publica el reparto de cuentas en largo ni el histórico de OI.
                    fh, oi, mk = res[s]["kraken_funding_h"], res[s]["kraken_oi"], _f(t.get("markPrice"))
                    res[s].update(ok=fh is not None, fuente="kraken", funding=fh * 8 if fh is not None else None,
                                  mark=mk, oi_usd=oi * mk if (oi is not None and mk) else None, oi_24h=None,
                                  largos_pct=None, top_largos_pct=None)
    except Exception:  # noqa: BLE001
        pass
    return res


def lectura_derivados(d, chg24=None):
    """Interpretación en lenguaje claro de los derivados de una moneda."""
    out = []
    f = d.get("funding")
    if f is not None:
        if f >= .0003:
            out.append(("w", "Funding muy alto: largos sobreapalancados, riesgo de barrida a la baja"))
        elif f >= .00015:
            out.append(("n", "Funding elevado: optimismo apalancado"))
        elif f <= -.0001:
            out.append(("w", "Funding negativo: dominan los cortos, posible rebote por cierre de cortos"))
    lp = d.get("largos_pct")
    if lp is not None:
        if lp >= 75:
            out.append(("w", f"{lp:.0f} % de cuentas en largo: posicionamiento muy cargado (señal contraria)"))
        elif lp <= 35:
            out.append(("n", f"Solo {lp:.0f} % de cuentas en largo: pesimismo extendido (señal contraria)"))
    oi = d.get("oi_24h")
    if oi is not None and chg24 is not None and abs(oi) >= 3:
        if oi > 0 and chg24 > 0:
            out.append(("n", f"Interés abierto +{oi:.0f} % con precio al alza: entran largos nuevos"))
        elif oi > 0 and chg24 < 0:
            out.append(("w", f"Interés abierto +{oi:.0f} % con precio a la baja: entran cortos nuevos"))
        elif oi < 0 and chg24 < 0:
            out.append(("n", f"Interés abierto {oi:.0f} %: cierre de posiciones (desapalancamiento)"))
        else:
            out.append(("n", f"Interés abierto {oi:.0f} % con subida: rebote por cierre de cortos"))
    return out


def _lin(x, a, b):
    """Mapea x de [a, b] a [0, 100] con recorte."""
    return max(0.0, min(100.0, (x - a) / (b - a) * 100))


def compuesto(fg=None, mc24=None, amplitud=None, funding_med=None, largos_med=None, pulso=None):
    """Índice de sentimiento 0-100 (0 = miedo extremo, 100 = codicia extrema) y sus componentes."""
    comp = []
    if fg is not None:
        comp.append(("Miedo y codicia", fg, .3, f"{fg:.0f}"))
    if mc24 is not None:
        comp.append(("Impulso 24 h del mercado", _lin(mc24, -6, 6), .15, f"{mc24:+.1f} %".replace(".", ",")))
    if amplitud is not None:
        comp.append(("Amplitud técnica", amplitud, .2, f"{amplitud:.0f}/100"))
    if funding_med is not None:
        comp.append(("Funding medio", _lin(funding_med, -.0001, .0003), .12, f"{funding_med * 100:+.4f} %".replace(".", ",")))
    if largos_med is not None:
        comp.append(("Posicionamiento minorista", _lin(largos_med, 35, 80), .08, f"{largos_med:.0f} % largos"))
    if pulso is not None:
        comp.append(("Pulso de noticias", (pulso + 100) / 2, .15, f"{pulso:+.0f}"))
    if not comp:
        return None
    w = sum(c[2] for c in comp)
    v = sum(c[1] * c[2] for c in comp) / w
    lab = ("Miedo extremo" if v < 20 else "Miedo" if v < 40 else "Neutral" if v < 60 else "Codicia" if v < 80 else "Codicia extrema")
    return {"valor": v, "lectura": lab, "componentes": [{"nombre": n, "valor": x, "peso": p / w, "dato": d} for n, x, p, d in comp],
            "t": int(time.time() * 1000)}
