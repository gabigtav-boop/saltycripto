"""Servicio en vivo: mantiene todo el estado que muestra la web.

Capas de actualización:
  · cada segundo   → precio del WebSocket, lectura técnica en vivo (vela en formación), zonas, semáforo,
                     fiabilidad y microestructura (flujo de órdenes, libro, momento en minutos)
  · cada minuto    → noticias y calendario (análisis fundamental), derivados y sentimiento compuesto
  · cada 10 min    → recarga completa del motor v21: velas, histórico y validación
El motor v21 (motor.py) es una traducción verificada contra el JavaScript original (tests/test_paridad.py)."""
import json
import pathlib
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import fuentes
from . import hora
from .envivo import Flujo
from .indicadores import ema, holes
from .motor import (LONG, SHORT, TFS, analyze, backtest, bias, book_sum, fin, gs, validate, zones)
from .noticias import Fundamental, resumen_impacto
from .prevision import prevision
from .historico import Historico
from .proyeccion import calibrar, compacto, proyectar
from .sentimiento import compuesto, derivados, lectura_derivados
from .validacion import con_intervalo

COINS = {"BTC": "XBT/EUR", "ETH": "ETH/EUR", "XRP": "XRP/EUR", "SOL": "SOL/EUR", "HYPE": "HYPE/EUR",
         "SUI": "SUI/EUR", "ZEC": "ZEC/EUR", "PAXG": "PAXG/EUR"}
WSV2 = {s: ("BTC/EUR" if s == "BTC" else v) for s, v in COINS.items()}   # nombres del WebSocket v2
CGID = {"BTC": "bitcoin", "ETH": "ethereum", "XRP": "ripple", "SOL": "solana", "HYPE": "hyperliquid",
        "ZEC": "zcash", "SUI": "sui", "PAXG": "pax-gold"}
TTL = {1: 60, 60: 120, 240: 300, 1440: 600, 10080: 1800}  # segundos de caché de velas
TF_MS = {t[0]: t[1] * 60000 for t in TFS}
CHART_N = 75
DERIV_LOG = pathlib.Path(__file__).resolve().parent.parent / "datos" / "derivados_historial.jsonl"
DERIV_LOG_MAX = 5_000_000      # al superar 5 MB se guarda como .old y se empieza otro
# Un dato de una fuente externa que lleva mucho sin renovarse no se usa como si fuera de ahora
EDAD_PRECIO_EXTERNO = 15 * 60          # precio de Coinbase o CoinGecko usado para contrastar el de Kraken
EDAD_DERIVADOS = 30 * 60               # derivados que entran en la previsión y en el sentimiento
EDAD_TICKER = 20                       # precio de Kraken de la carga completa: se pide de nuevo si es más viejo


def now_ms():
    return int(time.time() * 1000)


def hm(t=None):
    """Hora de Canarias (no la del equipo: en GitHub sería UTC)."""
    return hora.hm(t)


def hms(t=None):
    return hora.hms(t)


def clean(x):
    """JSON sin NaN/Infinity (el navegador no los acepta)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


# lo calculado en vivo se conserva mientras se recarga la moneda (si no, el rango y la lectura desaparecían
# cerca de un minuto cada 10 minutos, hasta que terminaba de recalibrarse)
CONSERVAR = ("vivo", "micro", "tkv", "calib", "proy", "prev")


def heredar(nuevo, viejo):
    for k in CONSERVAR:
        if k in viejo:
            nuevo[k] = viejo[k]
    return nuevo


ZK_WEB = ("lo", "hi", "mid", "t", "s", "proj", "weak")


def zona_web(z):
    """Zonas sin los campos internos que cambian con cada tic (distancia al precio, visitas…)."""
    if not z:
        return z
    return {"sup": [{k: g[k] for k in ZK_WEB if k in g} for g in z.get("sup", [])],
            "res": [{k: g[k] for k in ZK_WEB if k in g} for g in z.get("res", [])]}


def ligero(x):
    """Como clean(), y además redondea a 7 cifras significativas para aligerar lo que se envía a la web."""
    if isinstance(x, float):
        return float(f"{x:.7g}") if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: ligero(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [ligero(v) for v in x]
    return x


def vela_viva(raw, tf_ms, price, t):
    """Velas con la vela en formación actualizada al precio en vivo (y velas nuevas si el periodo cambió)."""
    if not raw or not price:
        return raw
    last = dict(raw[-1])
    out = raw[:-1]
    if t >= last["t"] + tf_ms:
        out = out + [last]
        k = (t - last["t"]) // tf_ms
        start = last["t"] + k * tf_ms
        prev_c = last["c"]
        for x in range(1, int(k)):
            ts = last["t"] + x * tf_ms
            out.append({"t": ts, "o": prev_c, "h": prev_c, "l": prev_c, "c": prev_c, "v": 0.0})
        last = {"t": start, "o": prev_c, "h": max(prev_c, price), "l": min(prev_c, price), "c": price, "v": 0.0}
    else:
        last["h"] = max(last["h"], price)
        last["l"] = min(last["l"], price)
        last["c"] = price
    return out + [last]


class Servicio:
    def __init__(self, kraken=None, net=True):
        self.k = kraken or fuentes.Kraken()
        self.lock = threading.RLock()
        self.data = {}
        self.pairs = {}
        self.prec = {}
        self.tk = {}
        self.cache = {}
        self.bt = {}
        self.cbp = {}
        self.cbc = {}
        self.mk = {}
        self.mkt = 0
        self.ctx = {}
        self.order = list(COINS)
        self.val = None  # None = calculando
        self.btcl = None
        self.off = set()
        self.busy = False
        self.bst = 0
        self.last = 0
        self.bar = "preparando…"
        self.err = None
        self.version = 0
        self.fast_v = 0
        self._snap = (None, None)
        self.net = net
        # tiempo real y capas nuevas
        self.flujo = None
        self.fund = Fundamental()
        self.noticias = []
        self.pulso = {}
        self.deriv = {}
        self.deriv_t = 0
        self._reg_t = 0
        self.con_ws = True      # False en la versión nube: sin conexión en vivo (todo por REST)
        self._main_lock = threading.Lock()   # una sola carga completa a la vez (antes, un plazo de 300 s dejaba entrar a otra)
        self.cbp_t, self.cbc_t, self.tk_t = {}, 0, 0
        self.sent = None
        self.rt_ms = None
        self.rt_t = 0
        self.inicio = time.time()
        self.rnews = {}
        self.calib = {}          # (moneda, tf) -> (clave de la última vela cerrada, calibración)
        self.hist = Historico(net=net)
        self._ciclo = 0

    # ---------- estado ----------
    def bump(self):
        with self.lock:
            self.version += 1

    def set_bar(self, s):
        with self.lock:
            self.bar = s
            self.version += 1

    def vis(self):
        return [s for s in self.order if s not in self.off]

    # ---------- Kraken REST ----------
    def find_pairs(self):
        d = self.k.get("AssetPairs")
        for s, ws in COINS.items():
            e = next(((k, v) for k, v in d.items() if v.get("wsname") == ws), None)
            self.pairs[s] = e[0] if e else None
            if e:
                self.prec[s] = (e[1].get("pair_decimals", 8), e[1].get("lot_decimals", 8))

    def ohlc(self, key, minutes):
        ck = (key, minutes)
        h = self.cache.get(ck)
        if h and time.time() - h[0] < TTL[minutes]:
            return h[1]
        r = fuentes.parse_ohlc(self.k.get(f"OHLC?pair={key}&interval={minutes}"))
        self.cache[ck] = (time.time(), r)
        return r

    def tick(self, key):
        """Último precio de Kraken. La foto de toda la carga se hizo al empezar: para las últimas monedas tendría ya cerca
        de un minuto, así que si es más vieja de EDAD_TICKER segundos se vuelve a pedir ese par."""
        if key in self.tk and (not self.tk_t or time.time() - self.tk_t < EDAD_TICKER):   # sin marca de edad (datos cargados a mano), se usa tal cual
            return float(self.tk[key]["c"][0])
        d = self.k.get("Ticker?pair=" + key)
        x = d[next(iter(d))]
        self.tk[key] = x
        return float(x["c"][0])

    def tick_all(self):
        try:
            self.tk = self.k.get("Ticker?pair=" + ",".join(p for p in self.pairs.values() if p))
            self.tk_t = time.time()
        except Exception:  # noqa: BLE001
            self.tk = {}

    # ---------- otras fuentes ----------
    def load_ctx(self):
        c = {}
        try:
            f = fuentes.jget("https://api.alternative.me/fng/?limit=8")["data"]
            c["fg"], c["fg1"] = f[0], f[1]
            if len(f) > 7:
                c["fg7"] = f[7]
            c["t_fg"] = now_ms()
        except Exception:  # noqa: BLE001
            pass
        try:
            d = fuentes.jget("https://api.coingecko.com/api/v3/global")["data"]
            c["dom"] = d["market_cap_percentage"]["btc"]
            c["mc"] = d["market_cap_change_percentage_24h_usd"]
            c["mcap"] = d["total_market_cap"].get("eur")
            c["vol"] = d["total_volume"].get("eur")
            c["t_cg"] = now_ms()
        except Exception:  # noqa: BLE001
            pass
        with self.lock:
            if c:
                self.ctx = {**self.ctx, **c}     # si CoinGecko limita las peticiones no se pierde la dominancia
            self.version += 1

    def cg_markets(self, reorder):
        try:
            d = fuentes.jget("https://api.coingecko.com/api/v3/coins/markets?vs_currency=eur&price_change_percentage=24h&ids=" + ",".join(CGID.values()))
            mk = {}
            for s, cid in CGID.items():
                x = next((y for y in d if y.get("id") == cid), None)
                if x:
                    mk[s] = {"p": x.get("current_price"), "mc": x.get("market_cap"), "rk": x.get("market_cap_rank"), "ch": x.get("price_change_percentage_24h")}
            with self.lock:
                self.mk = mk
                if reorder:
                    self.order = sorted(COINS, key=lambda s: -((mk.get(s) or {}).get("mc") or 0))
                self.mkt = time.time()
        except Exception:  # noqa: BLE001
            pass

    def cb_spot(self):
        def one(s):
            try:
                v = float(fuentes.jget(f"https://api.coinbase.com/v2/prices/{s}-EUR/spot")["data"]["amount"])
                if v > 0:
                    self.cbp[s] = v
                    self.cbp_t[s] = time.time()
            except Exception:  # noqa: BLE001
                pass
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(one, self.vis()))

    def cb_candles(self):
        out = {}
        for s in self.vis():
            try:
                rows = fuentes.parse_coinbase(fuentes.jget(f"https://api.exchange.coinbase.com/products/{s}-EUR/candles?granularity=86400"))
                a = analyze(rows)
                if a and a["score"] is not None:
                    out[s] = {"b": bias(a["score"])}
            except Exception:  # noqa: BLE001
                pass
            time.sleep(.16)
        self.cbc = out
        self.cbc_t = time.time()

    def p_coinbase(self, s):
        """Precio de Coinbase solo si es reciente (si la fuente cae, no se contrasta contra un dato de hace horas)."""
        t = self.cbp_t.get(s)
        return self.cbp.get(s) if t is None or time.time() - t < EDAD_PRECIO_EXTERNO else None

    def p_coingecko(self, s):
        return (self.mk.get(s) or {}).get("p") if not self.mkt or time.time() - self.mkt < EDAD_PRECIO_EXTERNO * 2 else None

    def b_coinbase(self, s):
        return (self.cbc.get(s) or {}).get("b") if not self.cbc_t or time.time() - self.cbc_t < 3 * 3600 else None

    def refresh_src(self):
        with self.lock:
            for s, o in self.data.items():
                if not o or not o.get("price"):
                    continue
                o["cbp"] = self.p_coinbase(s)
                o["cgp"] = self.p_coingecko(s)
                o["cbb"] = self.b_coinbase(s)
                if o.get("crit"):
                    fin(o)
            self.version += 1

    # ---------- análisis de una moneda (motor v21) ----------
    def run(self, sym, now=None):
        key = self.pairs.get(sym)
        if not key:
            return {"sym": sym, "nopair": True}
        price = self.tick(key)
        res, prev, errs, raws = {}, {}, [], {}
        r1h = r1d = book = None
        try:
            dp = self.k.get(f"Depth?pair={key}&count=500")
            book = book_sum(dp[next(iter(dp))], price)
        except Exception:  # noqa: BLE001
            pass
        for tid, mins, _w in TFS:
            try:
                raw = self.ohlc(key, mins)
                raws[tid] = raw
                if tid == "1h":
                    r1h = raw
                if tid == "1d":
                    r1d = raw
                a = analyze(raw)
                if not a:
                    errs.append(tid + " insuficiente")
                    continue
                t = now if now is not None else now_ms()
                if t - a["rows"][-1]["t"] > 3 * mins * 60000:
                    errs.append(tid + " obsoleto")
                    continue
                if holes(a["rows"], mins) > .2:
                    errs.append(tid + " con huecos")
                    continue
                res[tid] = a
                p = analyze(raw[:-1])
                if p:
                    prev[tid] = p
            except Exception:  # noqa: BLE001
                errs.append(tid + " sin datos")
        o = self.finish(sym, price, res, prev, errs, r1h, r1d, book, now)
        o["_raw"] = raws
        o["_prev"] = prev
        return o

    def finish(self, sym, price, res, prev, errs, r1h, r1d, book, now=None):
        now = now if now is not None else now_ms()
        d1 = res.get("1d")
        if not d1 and not res.get("4h"):
            return {"sym": sym, "fail": "sin velas suficientes en Kraken"}
        if abs(price / (d1 or res["4h"])["last"] - 1) > .35:
            return {"sym": sym, "fail": "precio fuera de rango respecto a las velas (dato sospechoso)"}
        ref24 = None
        if r1h and len(r1h) > 26:
            tc = now - 864e5
            for i in range(len(r1h) - 1, -1, -1):
                if r1h[i]["t"] <= tc:
                    if tc - r1h[i]["t"] < 3 * 36e5:
                        ref24 = r1h[i]["o"]
                    break
        chg = (price / ref24 - 1) * 100 if ref24 else None
        ts, tl, filt = gs(res, SHORT), gs(res, LONG), None
        macro = (1 if d1["last"] > d1["e200"] else -1) if (d1 and d1["e200"]) else 0
        pS, pL = gs(prev, SHORT), gs(prev, LONG)
        if ts is not None:
            if macro < 0 and ts > 59:
                ts, filt = 59, "EMA200 diaria"
            elif macro > 0 and ts < 41:
                ts, filt = 41, "EMA200 diaria"
            # filtro de mercado: las altcoins no se leen alcistas a corto con Bitcoin bajista
            if not filt and self.btcl == "BA" and sym not in ("BTC", "PAXG") and ts > 59:
                ts, filt = 59, "tendencia de Bitcoin"
        bS, bL = bias(ts, bias(pS)), bias(tl, bias(pL))
        if not d1:
            reg = None
        elif d1["rg"] == "trend":
            reg = "tendencia alcista" if d1["last"] > (d1["e200"] or d1["e50"]) else "tendencia bajista"
        else:
            reg = "lateral" if d1["rg"] == "range" else "transición"
        o = {"sym": sym, "price": price, "chg": chg, "ref24": ref24, "res": res, "errs": errs, "ts": ts, "tl": tl,
             "bS": bS, "bL": bL, "reg": reg, "filt": filt, "book": book, "cbp": self.p_coinbase(sym),
             "cgp": self.p_coingecko(sym), "cbb": self.b_coinbase(sym),
             "z": zones(d1, price, book, res.get("1w")) if d1 else None, "d1": d1, "raw1d": r1d,
             "short": bool(d1) and len(d1["rows"]) < 200, "t": now}
        return fin(o)

    # ---------- histórico ----------
    def bt_key(self, s, o):
        return s + str(o["raw1d"][-1]["t"])

    def add_bt(self, s):
        o = self.data.get(s)
        if not o or not o.get("raw1d"):
            return
        try:
            k = self.bt_key(s, o)
            if k not in self.bt:
                for viejo in [x for x in self.bt if x.startswith(s) and x != k and x[len(s):].isdigit()]:
                    del self.bt[viejo]
                self.bt[k] = backtest(o["raw1d"])
            r = self.bt[k]
        except Exception:  # noqa: BLE001
            r = None
        with self.lock:
            o["bt"] = r
            fin(o)
            self.version += 1

    def do_validate(self):
        with self.lock:
            self.val = con_intervalo(validate(self.data), self.data)
            self.version += 1

    # ---------- carga completa (cada 10 min) ----------
    def main(self, silent=False):
        if not self._main_lock.acquire(blocking=False):
            return                                   # ya hay una carga en marcha
        try:
            self._main(silent)
        finally:
            self._main_lock.release()

    def _main(self, silent=False):
        with self.lock:
            self.busy = True
            self.bst = time.time()
            self.err = None
            if not silent:
                self.data = {}
                self.btcl = None
                self.val = None
            self.version += 1
        try:
            try:
                if self.pairs.get("BTC") is None:
                    self.find_pairs()
                self.tick_all()
            except Exception:  # noqa: BLE001
                with self.lock:
                    if silent:
                        self.bar = "sin conexión · datos anteriores"
                    else:
                        self.err = "No se pudo conectar con Kraken. Revisa la conexión; se reintenta solo cada 45 s."
                return
            self.start_stream()
            ths = [threading.Thread(target=f, args=a) for f, a in ((self.load_ctx, ()), (self.cg_markets, (True,)), (self.cb_spot, ()))]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            self.bump()
            keys = [k for k in ["BTC"] + [k for k in self.order if k != "BTC"] if k not in self.off]
            cbt = threading.Thread(target=self.cb_candles)
            cbt.start()
            for s in keys:
                self.set_bar(f"cargando {s}…")
                try:
                    o = self.run(s)
                    viejo = self.data.get(s) or {}
                    if (o.get("fail") or o.get("nopair")) and silent and viejo.get("price"):
                        # un fallo puntual (precio sospechoso, Kraken que no responde) no sustituye a una moneda ya analizada
                        with self.lock:
                            viejo["recarga_fallo"] = o.get("fail") or "sin par"
                            self.version += 1
                        continue
                    if o.get("raw1d") and self.bt_key(s, o) in self.bt:
                        o["bt"] = self.bt[self.bt_key(s, o)]
                        fin(o)
                    with self.lock:
                        old = self.data.get(s) or {}
                        heredar(o, old)
                        self.data[s] = o
                        if s == "BTC" and o.get("bL"):
                            self.btcl = o["bL"]
                        self.version += 1
                except Exception as e:  # noqa: BLE001
                    with self.lock:
                        if not silent or s not in self.data:
                            self.data[s] = {"sym": s, "fail": str(e)}
                        self.version += 1
            cbt.join()
            self.refresh_src()
            self.sembrar_1m()
            self.set_bar(f"kraken · {hm()} · calculando histórico…")
            for s in keys:
                self.add_bt(s)
                self.calibrar_moneda(s)
            self.do_validate()
            with self.lock:
                self.last = time.time()
                self.bar = f"kraken · actualizado {hm(self.last)}"
        finally:
            with self.lock:
                self.busy = False
                self.version += 1

    def load_coin(self, s):
        try:
            o = self.run(s)
        except Exception as e:  # noqa: BLE001
            o = {"sym": s, "fail": str(e)}
        with self.lock:
            self.data[s] = o
            self.version += 1
        self.add_bt(s)
        self.calibrar_moneda(s)
        self.do_validate()

    def _calibrar(self, s, tf, rows, clave):
        """Calibra una temporalidad si hay velas cerradas nuevas; devuelve la calibración vigente."""
        k = (s, tf)
        prev = self.calib.get(k)
        if prev and prev[0] == clave:
            return prev[1]
        try:
            c = calibrar(rows, tf)
        except Exception as e:  # noqa: BLE001
            print(f"calibración {s} {tf}: {e!r}", flush=True)
            c = None
        self.calib[k] = (clave, c)
        return c

    def calibrar_moneda(self, s, solo_1m=False):
        """Calibración del rango y de las probabilidades de tocar niveles, por temporalidad, con histórico profundo
        (Binance/Coinbase) empalmado con Kraken. Se recalcula solo cuando cierra una vela nueva (la de 1 minuto, cada 5 minutos)."""
        o = self.data.get(s)
        if not o or not o.get("_raw"):
            return
        out = dict(o.get("calib") or {})
        tfs = ["1m"] if solo_1m else ["1m", "1h", "4h", "1d", "1w"]
        for tf in tfs:
            try:
                if tf == "1m":
                    rows = self.velas_1m(s)[:-1]
                    if len(rows) < 200:
                        continue
                    clave = rows[-1]["t"] // 300000
                else:
                    rows = (o["_raw"].get(tf) or [])[:-1]
                    if not rows:
                        continue
                    clave = rows[-1]["t"]
                if tf != "1w":
                    self.hist.actualizar(s, tf)
                    rows = self.hist.serie(s, tf, rows)
                c = self._calibrar(s, tf, rows, clave)
                if c:
                    out[tf] = c
            except Exception:  # noqa: BLE001
                pass
        with self.lock:
            o["calib"] = out

    def load_missing(self):
        for s in self.vis():
            if s not in self.data and s in self.pairs:
                self.load_coin(s)

    # ---------- flujo en vivo ----------
    def start_stream(self):
        if self.flujo or not self.net or not self.con_ws:
            return
        syms = [WSV2[s] for s in COINS if self.pairs.get(s)]
        prec = {WSV2[s]: self.prec[s] for s in COINS if s in self.prec}
        self.flujo = Flujo(syms, prec, on_update=None)
        self.flujo.start()

    def sembrar_1m(self):
        """Velas de 1 minuto (12 h) para el gráfico de 1m, la microestructura y la reacción a noticias."""
        if not self.flujo:
            return
        for s in COINS:
            key = self.pairs.get(s)
            if not key:
                continue
            try:
                self.flujo.sembrar_1m(WSV2[s], self.ohlc(key, 1))
            except Exception:  # noqa: BLE001
                pass

    def tiempo_real(self):
        """Recalcula cada segundo la lectura en vivo de todas las monedas."""
        t0 = time.perf_counter()
        now = now_ms()
        fl = self.flujo
        self._ciclo += 1
        lentos = self._ciclo % 10 == 1   # diario y semanal apenas cambian: cada 10 s
        with self.lock:
            items = list(self.data.items())
        for s, o in items:
            if not o or not o.get("res") or not o.get("d1"):
                continue
            ws = WSV2[s]
            p = (fl.precio(ws) if fl else None) or o.get("price")
            tkr = fl.ticker.get(ws) if fl else None
            prev_tf = (o.get("vivo") or {}).get("tf") or {}
            vivo = {}
            if p and abs(p / o["d1"]["last"] - 1) <= .35:
                raws = o.get("_raw") or {}
                for tid, _m, _w in TFS:
                    raw = raws.get(tid)
                    if not raw or tid not in o["res"]:
                        continue
                    if tid in ("1d", "1w") and not lentos and tid in prev_tf:
                        x = dict(prev_tf[tid])
                        v = dict(x["vela"])
                        v["h"], v["l"], v["c"] = max(v["h"], p), min(v["l"], p), p
                        x["vela"] = v
                        vivo[tid] = x
                        continue
                    rl = vela_viva(raw, TF_MS[tid], p, now)
                    a = analyze(rl, False)
                    if a and a["score"] is not None:
                        vivo[tid] = {"score": a["score"], "rsi": a["rsi"], "v": a["v"], "cats": a["cats"], "rg": a["rg"],
                                     "vela": rl[-1]}
                prev = o.get("vivo") or {}
                vts, vtl = gs(vivo, SHORT), gs(vivo, LONG)
                vivo_out = {"tf": vivo, "ts": vts, "tl": vtl,
                            "bS": bias(vts, prev.get("bS") or o.get("bS")), "bL": bias(vtl, prev.get("bL") or o.get("bL")), "t": now}
                z = zones(o["d1"], p, o.get("book"), o["res"].get("1w"))
                micro = fl.micro(ws) if fl else None
                with self.lock:
                    o["price"] = p
                    if o.get("ref24"):
                        o["chg"] = (p / o["ref24"] - 1) * 100
                    o["z"] = z
                    o["vivo"] = vivo_out
                    o["micro"] = micro
                    if tkr:
                        o["tkv"] = {k: tkr.get(k) for k in ("bid", "ask", "high", "low", "volume", "vwap", "change_pct")} | {"t": int(tkr.get("t", 0) * 1000)}
                    fin(o)
                    o["prev"] = prevision(o, self.deriv_fresco(s), self.pulso.get(s), self.sent)
                    proy = {}
                    for tf, c in (o.get("calib") or {}).items():
                        try:
                            x = proyectar(p, tf, c, o["prev"], z)
                        except Exception as e:  # noqa: BLE001
                            print(f"proyección {s} {tf}: {e!r}", flush=True)
                            x = None
                        if x:
                            proy[tf] = x
                    o["proy"] = proy
                    o["t"] = now
        with self.lock:
            self.fast_v += 1
            self.rt_ms = (time.perf_counter() - t0) * 1000
            self.rt_t = now

    # ---------- noticias, derivados y sentimiento (cada minuto) ----------
    def velas_ref(self):
        v1m, v1h = {}, {}
        for s in COINS:
            v1m[s] = self.velas_1m(s)
            o = self.data.get(s) or {}
            v1h[s] = (o.get("_raw") or {}).get("1h")
        return v1m, v1h

    def actualizar_fundamental(self):
        try:
            self.fund.actualizar()
        except Exception:  # noqa: BLE001
            pass
        self.recalc_noticias()

    def recalc_noticias(self):
        try:
            v1m, v1h = self.velas_ref()
            lst = self.fund.lista(v1m, v1h, n=120)
            pul = Fundamental.pulso([n for n in lst if now_ms() - n["t"] <= 864e5])
            rn = {a: resumen_impacto(lst, a, pul) for a in ["MERCADO"] + list(COINS)}
            with self.lock:
                self.noticias = lst
                self.pulso = pul
                self.rnews = rn
                self.version += 1
        except Exception:  # noqa: BLE001
            pass

    def actualizar_derivados(self):
        try:
            d = derivados(list(COINS))
            with self.lock:
                for s, x in d.items():
                    o = self.data.get(s) or {}
                    x["lectura"] = lectura_derivados(x, o.get("chg"))
                self.deriv = d
                self.deriv_t = now_ms()
            self.registrar_derivados(d)
        except Exception:  # noqa: BLE001
            pass
        self.calc_sentimiento()

    def velas_1m(self, s):
        """Velas de 1 minuto: del flujo en vivo si lo hay; si no, de la API REST de Kraken (últimas 720)."""
        if self.flujo:
            return self.flujo.velas(WSV2[s])
        key = self.pairs.get(s)
        if not key:
            return []
        try:
            return self.ohlc(key, 1)
        except Exception:  # noqa: BLE001
            return []

    def deriv_fresco(self, s):
        """Derivados de la moneda solo si se renovaron hace poco: si las fuentes caen, no se siguen usando los de hace horas."""
        return self.deriv.get(s) if self.deriv_t and now_ms() - self.deriv_t < EDAD_DERIVADOS * 1000 else None

    def registrar_derivados(self, d):
        """Una foto por hora de los derivados (funding, cuentas en largo, interés abierto). Las APIs solo ofrecen unos
        30 días de historia de estos datos: guardándolos aquí se acumula la que hará falta para estudiar si aportan
        algo a la predicción (ver ANALISIS_PREDICCION.md)."""
        if time.time() - self._reg_t < 3300:
            return
        self._reg_t = time.time()
        t = now_ms()
        lin = [json.dumps({"t": t, "s": s, **{k: x.get(k) for k in ("funding", "largos_pct", "top_largos_pct", "oi_usd", "oi_24h", "fuente")}},
                          separators=(",", ":")) for s, x in d.items() if x.get("ok")]
        if not lin:
            return
        try:
            DERIV_LOG.parent.mkdir(exist_ok=True)
            if DERIV_LOG.exists() and DERIV_LOG.stat().st_size > DERIV_LOG_MAX:
                DERIV_LOG.replace(DERIV_LOG.with_suffix(".jsonl.old"))
            with DERIV_LOG.open("a") as f:
                f.write("\n".join(lin) + "\n")
        except Exception:  # noqa: BLE001
            pass

    def calc_sentimiento(self):
        with self.lock:
            fg = self.ctx.get("fg")
            bl = [o.get("bL") for o in self.data.values() if o and o.get("bL") in ("AL", "NE", "BA")]
            amp = statistics.mean(100 if b == "AL" else 50 if b == "NE" else 0 for b in bl) if bl else None
            crypto = [s for s in COINS if s != "PAXG"]
            dv = {s: self.deriv_fresco(s) or {} for s in crypto}
            fund = [dv[s]["funding"] for s in crypto if dv[s].get("funding") is not None]
            lp = [dv[s]["largos_pct"] for s in crypto if dv[s].get("largos_pct") is not None]
            pm = self.pulso.get("MERCADO", {}).get("score")
            pb = self.pulso.get("BTC", {}).get("score")
            pulse = (pm if pb is None else pb if pm is None else (pm + pb) / 2) if (pm is not None or pb is not None) else None
            self.sent = compuesto(fg=float(fg["value"]) if fg else None, mc24=self.ctx.get("mc"), amplitud=amp,
                                  funding_med=statistics.median(fund) if fund else None,
                                  largos_med=statistics.median(lp) if lp else None, pulso=pulse)
            self.version += 1

    # ---------- bucles ----------
    def _loop_main(self):
        primera = True
        while True:
            try:
                if primera:
                    primera = False
                    self.main(False)
                elif not self.busy:
                    if not self.last:
                        if not self.data:
                            self.main(False)
                    elif time.time() - self.last > 600:
                        self.main(True)
                    elif not (self.flujo and self.flujo.conectado):
                        self.live_rest()
            except Exception as e:  # noqa: BLE001  (un fallo puntual no puede parar las recargas)
                print(f"ciclo principal: {e!r}", flush=True)
            time.sleep(45)

    def live_rest(self):
        """Respaldo por REST si el WebSocket cae (como el ciclo de 45 s de la v21)."""
        try:
            tk = self.k.get("Ticker?pair=" + ",".join(p for p in self.pairs.values() if p))
            with self.lock:
                for s, o in self.data.items():
                    key = self.pairs.get(s)
                    if not o or not o.get("d1") or key not in tk:
                        continue
                    p = float(tk[key]["c"][0])
                    if not (p > 0) or abs(p / o["d1"]["last"] - 1) > .35:
                        continue
                    o["price"] = p
                    if o.get("ref24"):
                        o["chg"] = (p / o["ref24"] - 1) * 100
                    o["z"] = zones(o["d1"], p, o.get("book"), o["res"].get("1w"))
                    fin(o)
                    o["t"] = now_ms()
                self.bar = f"kraken · respaldo REST {hms()}"
                self.version += 1
        except Exception:  # noqa: BLE001
            pass

    def _loop_rt(self):
        ultimo_error = 0
        while True:
            t = time.time()
            try:
                if self.data:
                    self.tiempo_real()
            except Exception as e:  # noqa: BLE001
                if t - ultimo_error > 60:       # se avisa, pero sin llenar el registro
                    ultimo_error = t
                    print(f"tiempo real: {e!r}", flush=True)
            time.sleep(max(.2, 1 - (time.time() - t)))

    def _loop_min(self):
        time.sleep(3)
        n = 0
        while True:
            try:
                self.actualizar_fundamental()   # noticias y derivados cada minuto (avisos a tiempo)
                n += 1
                self.actualizar_derivados()
                if n % 5 == 2 and self.last and not self.busy:
                    for s in self.vis():
                        self.calibrar_moneda(s, solo_1m=True)
                if time.time() - self.mkt > 300 and self.last:
                    self.load_ctx()
                    self.cg_markets(False)
                    self.cb_spot()
                    self.refresh_src()
            except Exception as e:  # noqa: BLE001
                print(f"ciclo por minuto: {e!r}", flush=True)
            time.sleep(60)

    def start(self):
        for f in (self._loop_main, self._loop_rt, self._loop_min):
            threading.Thread(target=f, daemon=True).start()

    # ---------- salidas para la web ----------
    def salud(self):
        fl = self.flujo.salud() if self.flujo else {"conectado": False}
        return {"ws": fl, "rt_ms": self.rt_ms, "rt_t": self.rt_t, "noticias": {k: dict(v) for k, v in list(self.fund.estado.items())}, "cal_ok": self.fund.cal_ok,
                "historico": self.hist.resumen(),
                "deriv_t": self.deriv_t, "deriv_ok": sum(1 for x in self.deriv.values() if x.get("ok")), "last": int(self.last * 1000),
                "uptime": int(time.time() - self.inicio)}

    def coin_json(self, o):
        if o.get("fail") or o.get("nopair"):
            return {k: v for k, v in o.items() if not k.startswith("_")}
        res = {}
        for tid, a in o["res"].items():
            x = {k: v for k, v in a.items() if k not in ("rows", "E20", "E50")}
            x["rows"] = [[r["t"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in a["rows"][-CHART_N:]]
            res[tid] = x
        out = {k: v for k, v in o.items() if k not in ("res", "d1", "raw1d", "bt", "calib") and not k.startswith("_")}
        out["res"] = res
        out["raw1d"] = [[r["h"], r["l"], r["c"]] for r in o["raw1d"]] if o.get("raw1d") else None
        if "bt" in o:
            out["bt"] = {k: v for k, v in o["bt"].items() if k != "SM"} if o["bt"] else None
        if o.get("vivo"):
            out["vivo"] = {k: v for k, v in o["vivo"].items() if k != "tf"}
            out["vivo"]["tf"] = {t: {k: v for k, v in x.items() if k != "vela"} for t, x in o["vivo"]["tf"].items()}
        if o.get("proy"):
            out["proy"] = {tf: compacto(x) for tf, x in o["proy"].items()}
        return out

    def snapshot(self, since=None):
        with self.lock:
            if since is not None and since == self.version:
                return None
            if self._snap[0] == self.version:
                return self._snap[1]
            st = {"v": self.version, "data": {s: self.coin_json(o) for s, o in self.data.items()}, "ctx": self.ctx,
                  "mk": self.mk, "order": self.order, "val": self.val if self.val is not None else "pending",
                  "bar": self.bar, "busy": self.busy, "last": int(self.last * 1000), "err": self.err,
                  "pairs": {s: (p is not None) for s, p in self.pairs.items()},
                  "noticias": self.noticias, "pulso": self.pulso, "cal": self.fund.cal, "deriv": self.deriv,
                  "sent": self.sent, "rnews": self.rnews}
            body = json.dumps(ligero(st), ensure_ascii=False, separators=(",", ":")).encode()
            self._snap = (self.version, body)
            return body

    def tick_payload(self, sel=None, tf=None, memo=None):
        """Lo que cambia cada segundo (se envía por SSE).
        · Solo la moneda abierta lleva su proyección, y completa solo en la temporalidad que se está viendo.
        · Con `memo` (uno por conexión) se envía de cada moneda solo lo que ha cambiado desde el envío anterior.
        · Los números van redondeados a 7 cifras significativas (precisión de sobra para precios y porcentajes)."""
        with self.lock:
            coins = {}
            for s, o in self.data.items():
                if not o or not o.get("price"):
                    continue
                vv = o.get("vivo") or {}
                coins[s] = {"price": o["price"], "chg": o.get("chg"), "z": zona_web(o.get("z")), "tm": o.get("tm"), "crit": o.get("crit"),
                            "conf": o.get("conf"), "tkv": o.get("tkv"),
                            "vivo": {k: v for k, v in vv.items() if k != "tf"} | {"tf": {t: {"score": x["score"], "rsi": x["rsi"], "v": x["v"], "cats": x["cats"]} for t, x in (vv.get("tf") or {}).items()}} if vv else None,
                            "micro": o.get("micro"), "prev": o.get("prev")}
            out = {"v": self.version, "f": self.fast_v, "t": now_ms(), "coins": coins, "salud": self.salud(), "bar": self.bar,
                   "busy": self.busy}
            if sel in self.data and self.flujo:
                o = self.data[sel]
                ws = WSV2[sel]
                velas = {t: x["vela"] for t, x in ((o.get("vivo") or {}).get("tf") or {}).items()}
                v1 = self.flujo.velas1m[ws]
                if v1:
                    k = max(v1)
                    velas["1m"] = dict(v1[k])
                proy = o.get("proy") or {}
                proy = {k: (x if k == tf else compacto(x)) for k, x in proy.items()}
                out["sel"] = {"sym": sel, "velas": velas, "proy": proy}
            out = ligero(out)
        if memo is not None:
            if memo.get("_n", 0) % 30 == 0:      # cada 30 s, envío completo (por robustez)
                memo.clear()
            memo["_n"] = memo.get("_n", 0) + 1
            for s, c in out["coins"].items():
                for k in list(c):
                    if k in ("price", "chg"):
                        continue
                    js = json.dumps(c[k], separators=(",", ":"))
                    if memo.get((s, k)) == js:
                        del c[k]
                    else:
                        memo[(s, k)] = js
            js = json.dumps(out["salud"], separators=(",", ":"))
            if memo.get("salud") == js:
                del out["salud"]
            else:
                memo["salud"] = js
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))

    def velas(self, sym, tf):
        """Velas completas para el gráfico, con medias y la vela en formación al precio actual."""
        o = self.data.get(sym) or {}
        if tf == "1m":
            rows = self.velas_1m(sym)
        else:
            raw = (o.get("_raw") or {}).get(tf) or []
            vv = ((o.get("vivo") or {}).get("tf") or {}).get(tf)
            rows = raw[:-1] + [vv["vela"]] if (raw and vv and vv["vela"]["t"] == raw[-1]["t"]) else list(raw)
        rows = rows[-720:]
        cl = [r["c"] for r in rows]
        out = {"sym": sym, "tf": tf, "rows": [[r["t"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in rows]}
        if len(cl) >= 2:
            out["e20"] = ema(cl, 20)
            out["e50"] = ema(cl, 50)
            out["e200"] = ema(cl, 200) if len(cl) >= 200 else None
        return json.dumps(clean(out), separators=(",", ":"))
