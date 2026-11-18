"""Servicio en vivo: mantiene todo el estado que muestra la web.

Capas de actualización:
  · cada segundo   → precio del WebSocket, lectura técnica en vivo (vela en formación), zonas, semáforo,
                     fiabilidad y microestructura (flujo de órdenes, libro, momento en minutos)
  · cada minuto    → noticias y calendario (análisis fundamental), derivados y sentimiento compuesto
  · cada 10 min    → recarga completa del motor v21: velas, histórico y validación
El motor v21 (motor.py) es una traducción verificada contra el JavaScript original (tests/test_paridad.py)."""
import json
import pathlib
import sqlite3
import math
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import fuentes
from . import hora
from .decision import Registro, decision, FRESCO_MS
from .envivo import Flujo
from .indicadores import ema, holes
from .motor import (LONG, SHORT, TFS, analyze, backtest, bias, book_sum, fin, gs, validate, zones)
from .noticias import Fundamental, resumen_impacto
from .prevision import prevision
from .historico import Historico
from .proyeccion import calibrar, compacto, proyectar
from .sentimiento import KF_SYM, compuesto, derivados, lectura_derivados
from .avisos import Avisos
from .catalogo import CATALOGO, CRIPTO, campo
from .evidencias import ACTIVOS as EV_ACTIVOS, GRACIA_WS, Almacen, construir_emision
from .validacion import con_intervalo, intervalo_ic

COINS = campo("kraken")           # símbolo → wsname de la API REST de Kraken (todo sale del catálogo)
WSV2 = campo("ws")                # nombres del WebSocket v2
CGID = campo("coingecko")
TTL = {1: 60, 60: 120, 240: 300, 1440: 600, 10080: 1800}  # segundos de caché de velas
TF_MS = {t[0]: t[1] * 60000 for t in TFS}
CHART_N = 75
DERIV_LOG = pathlib.Path(__file__).resolve().parent.parent / "datos" / "derivados_historial.jsonl"
DERIV_LOG_MAX = 5_000_000      # al superar 5 MB se guarda como .old y se empieza otro
# Un dato de una fuente externa que lleva mucho sin renovarse no se usa como si fuera de ahora
EDAD_PRECIO_EXTERNO = 15 * 60          # precio de Coinbase o CoinGecko usado para contrastar el de Kraken
EDAD_DERIVADOS = 30 * 60               # derivados que entran en la previsión y en el sentimiento
EDAD_TICKER = 20                       # precio de Kraken de la carga completa: se pide de nuevo si es más viejo
REST_1M_CADA = 10 * 60_000             # velas REST de 1 min para las evidencias (y tras cada reconexión)
HISTORIAL_CADA = 5 * 60_000            # validación interna con lo verificado (cuánta evidencia respalda las proyecciones)


def confirmado_rest(x):
    """Un último precio del ticker REST de Kraken solo se da por vigente si está dentro de la horquilla del momento
    (compra-venta ±0,1 %). En un par tranquilo, el último negociado puede ser de hace horas y estar fuera del libro: antes
    se marcaba como «de ahora» al recibirlo."""
    try:
        c, a, b = float(x["c"][0]), float(x["a"][0]), float(x["b"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return 0 < b <= a and b * .999 <= c <= a * 1.001


def deriv_mercado(s, fuente):
    """Mercado del que proceden los derivados que se muestran como contexto (no son el mercado spot analizado)."""
    return {"binance": f"Binance USDⓈ-M · perpetuo {s}USDT", "bybit": f"Bybit · perpetuo lineal {s}USDT",
            "okx": f"OKX · perpetuo {s}-USDT-SWAP", "kraken": f"Kraken Futures · {KF_SYM.get(s, s)}"}.get(fuente, fuente)


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
    if "_observadas" in viejo:
        nuevo["_observadas"] = {tf: {t: vela for t, vela in velas.items() if t >= raw[-1]["t"]}
                                for tf, velas in viejo["_observadas"].items()
                                if (raw := nuevo.get("_raw", {}).get(tf))}
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
    def __init__(self, kraken=None, net=True, almacen=None, avisos=None):
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
        self.con_ws = True      # False en la versión nube: sin conexión en vivo (todo por REST)
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
        self.registro = Registro() if net else None
        self.error_registro = None
        self.tick_recibido = {}
        # tiempo real y capas nuevas
        self.flujo = None
        self.fund = Fundamental()
        self.noticias = []
        self.pulso = {}
        self.deriv = {}
        self.deriv_t = 0
        self._reg_t = 0
        self._main_lock = threading.Lock()   # una sola carga completa a la vez (antes, un plazo de 300 s dejaba entrar a otra)
        self.cbp_t, self.cbc_t, self.tk_t = {}, 0, 0
        self.sent = None
        self.rt_ms = None
        self.rt_t = 0
        self.inicio = time.time()
        self.rnews = {}
        self.calib = {}          # (moneda, tf) -> (clave de la última vela cerrada, calibración)
        self.hist = Historico(net=net)
        self.bt_largo = {}       # moneda -> (clave, muestras del backtest sobre toda la historia diaria)
        self._ciclo = 0
        # evidencias (proyecciones guardadas y su verificación) y avisos trazables de BTC y ETH
        self.evid, self.avisos, self.ev_error = almacen, avisos, None
        if net and (almacen is None or avisos is None):
            try:
                self.evid = self.evid or Almacen()
                self.avisos = self.avisos or Avisos()
            except (OSError, sqlite3.Error) as e:
                self.ev_error = f"no se pudo abrir la base de evidencias: {e}"
        self.av_ultimo = self.avisos.ultimo_id() if self.avisos else 0
        self._sin_emitir = {}
        self._rest_1m_t = 0
        self._reconex_vistas = 0
        self.hist_ev, self._hist_t, self._hist_cambios = None, 0, -1   # validación interna (al cambiar o cada 5 min)
        self.calidad_t, self._calidad = 0, None

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
            self.tick_recibido[key] = (int(self.tk_t * 1000) if self.tk_t else now_ms()) if confirmado_rest(self.tk[key]) else 0
            return float(self.tk[key]["c"][0])
        d = self.k.get("Ticker?pair=" + key)
        x = d[next(iter(d))]
        self.tk[key] = x
        self.tick_recibido[key] = now_ms() if confirmado_rest(x) else 0
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
        # hora del precio: la de recepción del ticker REST solo si su último negociado está dentro de la horquilla;
        # si no, 0 (no vigente) hasta que el WebSocket traiga una operación o el libro lo confirme
        rec = self.tick_recibido.get(key, now_ms())
        o["_precio_t"] = (now if now is not None else rec) if rec else 0
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
            if not filt and self.btcl == "BA" and sym != "BTC" and sym in CRIPTO and ts > 59:
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

    def muestras_largas(self):
        """Backtest (la misma función que usa la pantalla) sobre TODA la historia diaria de cada moneda. Se guarda por moneda y
        solo se rehace cuando cierra una vela diaria nueva; se calcula fuera del cerrojo."""
        out = {}
        for s, o in list(self.data.items()):
            if not o or not o.get("raw1d"):
                continue
            try:
                rows = self.hist.serie(s, "1d", o["raw1d"])
                clave = (rows[-1]["t"], len(rows))
                c = self.bt_largo.get(s)
                if not c or c[0] != clave:
                    b = backtest(rows)
                    c = (clave, b["SM"] if b else None)
                    self.bt_largo[s] = c
                if c[1]:
                    out[s] = {"bt": {"SM": c[1]}}
            except Exception as e:  # noqa: BLE001
                print(f"historia completa {s}: {e!r}", flush=True)
        return out

    def do_validate(self):
        try:
            largo = intervalo_ic(self.muestras_largas())
        except Exception as e:  # noqa: BLE001
            print(f"validación larga: {e!r}", flush=True)
            largo = None
        with self.lock:
            self.val = con_intervalo(validate(self.data), self.data, largo)
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

        with self.lock:
            items = list(self.data.items())
        for s, o in items:
            if not o or not o.get("res") or not o.get("d1"):
                if o:
                    o["decision"] = decision(o, now)
                    self.registrar_decision(s, o, now)
                continue
            ws = WSV2[s]
            p = o.get("price")
            stamp = o.get("precio_t", o.get("_precio_t", o.get("t", 0)))
            fuente = o.get("precio_fuente", "REST")
            ws_price = fl.precio(ws) if fl else None
            ticker = fl.ticker.get(ws) if fl else None
            if ws_price and ticker and int(ticker["t"] * 1000) >= stamp and abs(ws_price / o["d1"]["last"] - 1) <= .35:
                p, stamp, fuente = ws_price, int(ticker["t"] * 1000), "WebSocket"
            conf = fl.confirmacion(ws, p) if fl and p else None
            if conf and int(conf * 1000) > stamp:
                stamp = int(conf * 1000)     # el libro en vivo confirma que el último precio sigue vigente
            stamp = min(stamp, now)          # el hilo del WebSocket puede apuntar una hora posterior al inicio del ciclo
            op = (getattr(fl, "ultima_op", None) or {}).get(ws) if fl else None
            with self.lock:
                o["precio_t"], o["precio_fuente"] = stamp, fuente
                o["procedencia"] = {"bolsa": "Kraken", "par": ws, "fuente_precio": fuente, "precio_t": stamp,
                                    "ultima_op_t": op and op["t"], "ultima_op_p": op and op["p"],
                                    "ultima_op_llegada": op and op.get("recibido"),
                                    "confirmacion_t": int(conf * 1000) if conf else None, "calculo_t": now}
                o["fuentes"] = self.estado_fuentes(s, o, now)
            if not 0 <= now - stamp <= FRESCO_MS:
                with self.lock:
                    if o.get("prev"):
                        o["prev"]["obsoleta"] = True
                    o["micro"] = None
                    o["proy"] = {}
                    o["decision"] = decision(o, now)
                    o["procedencia"]["calculo_t"] = None          # no se recalcula con un precio caducado
                self.registrar_decision(s, o, now)
                self.evidencias_en_vivo(s, o, now, False, op)
                continue
            tkr = fl.ticker.get(ws) if fl else None
            prev_tf = (o.get("vivo") or {}).get("tf") or {}
            vivo = {}
            if p and abs(p / o["d1"]["last"] - 1) <= .35:
                raws = o.get("_raw") or {}
                for tid, _m, _w in TFS:
                    raw = raws.get(tid)
                    if not raw or tid not in o["res"]:
                        continue
                    rl = vela_viva(raw, TF_MS[tid], p, stamp)
                    anteriores = o.setdefault("_observadas", {}).setdefault(tid, {})
                    vela = rl[-1]
                    conocida = anteriores.get(vela["t"])
                    if conocida:
                        vela["h"] = max(vela["h"], conocida["h"])
                        vela["l"] = min(vela["l"], conocida["l"])
                        if vela["t"] > raw[-1]["t"]:
                            vela["o"] = conocida["o"]
                    elif vela["t"] > raw[-1]["t"]:
                        previa = anteriores.get(vela["t"] - TF_MS[tid])
                        if previa:
                            vela["o"] = previa["c"]
                            vela["h"], vela["l"] = max(p, previa["c"]), min(p, previa["c"])
                    anteriores[vela["t"]] = vela
                    if vela["t"] != raw[-1]["t"]:
                        # Conservar también la última vela cerrada durante este tramo en vivo.
                        rl = [anteriores.get(v["t"], v) if v["t"] >= raw[-1]["t"] else v for v in rl]
                    clave = (id(raw), vela["t"], vela["h"], vela["l"], vela["c"], vela["v"])
                    cache = o.setdefault("_analisis_vivo", {})
                    previa_tf = prev_tf.get(tid)
                    if previa_tf and cache.get(tid) == clave:
                        vivo[tid] = dict(previa_tf, vela=vela)
                        continue
                    if (tid in ("1d", "1w") and self._ciclo % 10 != 1 and previa_tf
                            and cache.get(tid) and previa_tf["vela"]["t"] == vela["t"]):
                        # Mantener la cadencia original del indicador lento; la vela sí sigue cada tic.
                        vivo[tid] = dict(previa_tf, vela=vela)
                        continue
                    cache[tid] = clave
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
                    try:
                        o["prev"] = prevision(o, self.deriv_fresco(s), self.pulso.get(s), self.sent)
                    except Exception as e:  # noqa: BLE001  (un dato defectuoso de una moneda no debe parar el ciclo de las demás)
                        print(f"previsión {s}: {e!r}", flush=True)
                        if o.get("prev"):
                            o["prev"]["obsoleta"] = True            # se conserva la última válida, marcada como desactualizada
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
                    o["decision"] = decision(o, now)
                self.registrar_decision(s, o, now)
                self.evidencias_en_vivo(s, o, now, bool(fl and getattr(fl, "conectado", False)), op)
        cal = self.calidad(now)
        cal.pop("t", None)
        with self.lock:
            self._calidad = cal
            self.fast_v += 1
            self.rt_ms = (time.perf_counter() - t0) * 1000
            self.rt_t = now

    # ---------- procedencia, evidencias y avisos ----------
    def estado_fuentes(self, s, o, now):
        """Cada fuente del análisis de una moneda, si está vigente y qué parte del análisis depende de ella. Si una fuente
        caduca, la página lo dice y los avisos que dependen de ella quedan suspendidos."""
        fl, ws = self.flujo, WSV2[s]
        pt = o.get("precio_t") or 0
        precio_ok = bool(pt) and 0 <= now - pt <= FRESCO_MS
        conectado = bool(fl and getattr(fl, "conectado", False))
        lb = (getattr(fl, "libros", None) or {}).get(ws) if fl else None
        libro_ok = bool(conectado and lb and lb.valido and time.time() - lb.t <= 120)
        r1h = (o.get("_raw") or {}).get("1h") or []
        v1h_ok = bool(r1h) and now - r1h[-1]["t"] <= 3 * 3600000
        d = self.deriv_fresco(s)
        dm = self.deriv.get(s) or {}
        out = [
            {"k": "precio", "n": f"Precio Kraken {ws}", "ok": precio_ok, "t": pt or None,
             "afecta": "decisión spot, proyección, evidencias y avisos"},
            {"k": "ws", "n": "Flujo en vivo (WebSocket)", "ok": conectado, "t": int(fl.ultimo * 1000) if fl and getattr(fl, "ultimo", 0) else None,
             "afecta": "operaciones, velas de 1 min y confirmación por libro (si cae: precio por REST cada 45 s)"},
            {"k": "libro", "n": "Libro verificado (CRC32)", "ok": libro_ok, "t": int(lb.t * 1000) if lb and lb.t else None,
             "afecta": "microestructura y confirmación del precio"},
            {"k": "velas1h", "n": "Velas de 1 h (REST)", "ok": v1h_ok, "t": r1h[-1]["t"] if r1h else None,
             "afecta": "proyección 1 h · 24 h y lectura técnica de 1 h"},
            {"k": "deriv", "n": "Derivados" + (f" · {deriv_mercado(s, dm.get('fuente'))}" if dm.get("fuente") else ""),
             "ok": d is not None, "t": self.deriv_t or None, "afecta": "factor derivados de la previsión y sentimiento"}]
        susp = []
        if s in EV_ACTIVOS:
            if not (precio_ok and conectado):
                susp += ["cruce de niveles", "cambio de contexto"]
            if not (precio_ok and v1h_ok):
                susp.append("emisión de la proyección horaria")
        return {"lista": out, "suspendidos": susp}

    def contexto_aviso(self, s, o):
        """Foto del contexto en el momento de un aviso (lo que la pantalla mostraba)."""
        p = o.get("prev") or {}
        pr = (o.get("proy") or {}).get("1h") or {}
        z = o.get("z") or {}
        return clean({"precio": o.get("price"), "precio_t": o.get("precio_t"), "procedencia": o.get("procedencia"),
                      "decision": o.get("decision"), "semaforo": (o.get("tm") or {}).get("v"),
                      "prevision": {k: p.get(k) for k in ("dir", "conf", "score", "cobertura", "obsoleta")},
                      "sesgo": {"corto": o.get("bS"), "largo": o.get("bL")},
                      "proyeccion_1h_24h": {k: pr.get(k) for k in ("b50", "b80", "sd_pct")} if pr else None,
                      "zonas": {k: [{"lo": g["lo"], "hi": g["hi"]} for g in z.get(k, [])[:2]] for k in ("sup", "res")},
                      "fuentes": o.get("fuentes")})

    def activos_ev(self):
        """Monedas del catálogo que pasan ahora por el flujo de evidencias: con par en Kraken y no ocultas."""
        return [s for s in EV_ACTIVOS if self.pairs.get(s) and s not in self.off]

    def calidad(self, now=None):
        """Validación interna para la lectura de ahora. Tres cosas distintas:
        · frescura   qué datos se pueden usar ahora (cotización ≤ 120 s y previsión recalculada; derivados ≤ 30 min);
        · cobertura  qué parte del mercado representa la lectura (activos y capitalización con datos vigentes);
        · historial  cuánta evidencia verificada respalda las proyecciones (cripto/evidencias.historial, cada 5 min).
        Devuelve también los mensajes cortos que la página muestra SOLO cuando cambian la lectura."""
        now = now or now_ms()
        with self.lock:
            vis = [s for s in self.order if s not in self.off and s in COINS]
            vig, cad = [], []
            for s in vis:
                o = self.data.get(s) or {}
                pt = o.get("precio_t")
                if o.get("fail") or o.get("nopair"):
                    cad.append({"s": s, "motivo": "sin datos de Kraken"})
                elif not o.get("price") or not o.get("res"):
                    cad.append({"s": s, "motivo": "cargando"})
                elif not pt or not 0 <= now - pt <= FRESCO_MS:
                    cad.append({"s": s, "motivo": "cotización sin renovar", "desde": pt or None})
                elif (o.get("prev") or {}).get("obsoleta"):
                    cad.append({"s": s, "motivo": "previsión sin recalcular"})
                else:
                    vig.append(s)
            mc = {s: (self.mk.get(s) or {}).get("mc") or 0 for s in vis if s in CRIPTO}
            tot = sum(mc.values())
            der_ok = bool(self.deriv_t) and now - self.deriv_t <= EDAD_DERIVADOS * 1000
            hist = self.hist_ev
        cargando = [x["s"] for x in cad if x["motivo"] == "cargando"]
        viejos = [x["s"] for x in cad if x["motivo"] != "cargando"]
        nombres = lambda L: ", ".join(L[:-1]) + " y " + L[-1] if len(L) > 1 else (L[0] if L else "")   # noqa: E731
        lab = lambda s: CATALOGO[s].get("etiqueta", s)                                                  # noqa: E731
        msg_m = []
        if viejos:
            msg_m.append(f"Lectura parcial por datos desactualizados en {nombres([lab(s) for s in viejos])}: "
                         f"el resumen usa {len(vig)} de {len(vis)} activos.")
        elif cargando and vig:
            msg_m.append(f"Lectura parcial: {nombres([lab(s) for s in cargando])} aún sin cargar.")
        if self.deriv_t and not der_ok:
            msg_m.append(f"Derivados sin renovar desde hace {round((now - self.deriv_t) / 60000)} min: no entran en la lectura.")
        out = {"t": now, "vigentes": vig, "caducados": cad,
               "cobertura": {"activos": [len(vig), len(vis)],
                             "capitalizacion": round(sum(mc[s] for s in vig if s in mc) / tot, 3) if tot else None},
               "derivados": {"ok": der_ok, "t": self.deriv_t or None},
               "parcial": bool(cad) or (bool(self.deriv_t) and not der_ok),
               "mensajes": {"mercado": msg_m, "datos": self._mensajes_historial(hist, now), "estudio": {}}}
        for x in cad:
            if x["motivo"] != "cargando":
                out["mensajes"]["estudio"][x["s"]] = ("Datos desactualizados: la decisión, la proyección y los avisos de este "
                                                      "activo quedan en suspenso hasta recibir una cotización nueva.")
        if hist:
            for s, r in hist["activos"].items():
                if r["estado"] == "desviada" and s not in out["mensajes"]["estudio"]:
                    out["mensajes"]["estudio"][s] = (
                        f"En {r['n_diarias']} días verificados, el rango del 80 % contuvo el precio el {round(r['cob80'] * 100)} % "
                        f"de las veces: la proyección de 24 h ha sido más {r['sentido']} de lo que indica.")
            out["historial"] = {"estado": hist["global"]["estado"], "activos": hist["global"]["activos"],
                                "por_activo": {s: {k: r[k] for k in ("estado", "n_diarias", "n_horarias", "pendientes", "sin_seguimiento")}
                                               for s, r in hist["activos"].items()}}
        return out

    @staticmethod
    def _mensajes_historial(hist, now):
        """Las líneas de la pantalla Datos: solo lo que cambia cómo leer las proyecciones."""
        if not hist:
            return []
        A, G, minimo = hist["activos"], hist["global"], hist["muestra_min_dia"]
        lin = []
        if G["estado"] == "insuficiente":
            ver = sum(r["n_horarias"] for r in A.values())
            if not ver:
                pend = sum(r["pendientes"] for r in A.values())
                lin.append("Historial aún insuficiente para valorar las proyecciones: " +
                           (f"{pend} emitidas, ninguna ha vencido todavía." if pend else "aún no hay proyecciones guardadas."))
            else:
                mx = max(r["n_diarias"] for r in A.values())
                lin.append(f"Historial aún insuficiente para valorar las proyecciones: {mx} de {minimo} días verificados "
                           f"sin solapar por activo ({ver} verificaciones horarias en total).")
        else:
            pc = lambda v: f"{round(v * 100)} %"          # noqa: E731
            lin.append(f"Proyecciones de 24 h: el rango del 80 % contuvo el precio en el {pc(G['cob80'])} de los días "
                       f"({pc(G['ic80'][0])}–{pc(G['ic80'][1])}; {G['activos']} activos con el mismo peso)" +
                       (", en línea con lo indicado." if G["estado"] == "en_linea" else ", lejos de lo indicado."))
            desv = [s for s, r in A.items() if r["estado"] == "desviada"]
            if desv:
                lin.append("Rango desviado en " + ", ".join(desv) + ": ver el diagnóstico.")
        sin = {s: r for s, r in A.items() if r["sin_seguimiento_48h"]}
        if sin:
            n = sum(r["sin_seguimiento_48h"] for r in sin.values())
            causa = next(iter(sin.values()))["motivo_sin_seguimiento"] or ""
            lin.append(f"{n} proyecci{'ones' if n > 1 else 'ón'} sin seguimiento en las últimas 48 h"
                       f" ({', '.join(sin)}){': ' + causa.split(':')[0].lower() if causa else ''}.")
        return lin

    def evidencias_en_vivo(self, s, o, now, conectado, op):
        """Cada segundo, para cada moneda activa del catálogo: la emisión horaria de la proyección y la detección de avisos."""
        if s not in EV_ACTIVOS or s in self.off:
            return
        fresco = conectado and bool(o.get("precio_t")) and 0 <= now - o["precio_t"] <= FRESCO_MS
        try:
            if self.evid:
                hora = now - now % 3600000
                if not self.evid.emitida(s, hora):
                    pr = (o.get("proy") or {}).get("1h")
                    with self.lock:
                        em, motivo = construir_emision(s, o, ligero(compacto(pr)) if pr else None,
                                                       (o.get("calib") or {}).get("1h"), now, o.get("procedencia"))
                    if em:
                        self.evid.emitir(em)
                        self._sin_emitir.pop((s, hora), None)
                    else:
                        self._sin_emitir[(s, hora)] = motivo
            if self.avisos:
                if self.avisos.observar(s, op, o.get("decision"), fresco, now, lambda: self.contexto_aviso(s, o)):
                    self.av_ultimo = self.avisos.ultimo_id()
            self.ev_error = None
        except (OSError, ValueError, sqlite3.Error) as e:
            self.ev_error = str(e)[:160]

    def sync_rest_1m(self, ahora):
        """Velas de 1 minuto de Kraken por REST (las últimas 12 h, con los minutos sin operaciones): rellenan los huecos
        de conexión y quedan guardadas con su procedencia («rest»)."""
        for s in self.activos_ev():
            key = self.pairs.get(s)
            try:
                r = self.k.get(f"OHLC?pair={key}&interval=1")
                k = next(x for x in r if x != "last")
                filas = fuentes.sanear([{"t": int(x[0]) * 1000, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
                                         "c": float(x[4]), "v": float(x[6]), "n": int(x[7])} for x in r[k]])
                self.evid.guardar_rest(s, filas, int(r["last"]) * 1000, ahora)
            except Exception as e:  # noqa: BLE001
                print(f"velas REST 1 min {s}: {e!r}", flush=True)
        self._rest_1m_t = ahora

    def ciclo_evidencias(self, ahora=None):
        """Cada 30 s: guarda las velas de 1 min cerradas, la cobertura, el volumen y el libro de BTC y ETH; pide velas REST
        cada 10 min o tras una reconexión; anota las horas sin emisión y evalúa las proyecciones vencidas."""
        if not self.evid:
            return
        ahora = ahora or now_ms()
        try:
            self.evid.latido(ahora)
            fl = self.flujo
            if fl:
                limite = ahora - GRACIA_WS
                for s in self.activos_ev():
                    ws = WSV2[s]
                    self.evid.guardar_ws(s, fl.tomar_cerradas(ws, limite), fl.cobertura_ws(ws, ahora), limite, ahora)
                    self.evid.guardar_volumen(s, fl.tomar_agregados(ws, limite))
                    self.evid.guardar_libro(s, fl.profundidad(ws), ahora)
            reconex = fl.reconexiones if fl else 0
            if self.net and self.pairs and (ahora - self._rest_1m_t >= REST_1M_CADA or reconex != self._reconex_vistas):
                self._reconex_vistas = reconex
                self.sync_rest_1m(ahora)
            hora = ahora - ahora % 3600000
            for (s, h), motivo in list(self._sin_emitir.items()):
                if h < hora:
                    self.evid.omision(s, h, motivo, ahora)
                    self._sin_emitir.pop((s, h), None)
            self.evid.evaluar_pendientes(ahora)
            if self.evid.cambios != self._hist_cambios or ahora - self._hist_t >= HISTORIAL_CADA:
                self._hist_cambios = self.evid.cambios
                self.hist_ev = self.evid.historial(ahora, tuple(self.activos_ev()) or EV_ACTIVOS)
                self._hist_t = ahora
            self.evid.compactar(ahora)
            self.ev_error = None
        except (OSError, ValueError, sqlite3.Error) as e:
            self.ev_error = str(e)[:160]
            print(f"evidencias: {e!r}", flush=True)

    def _loop_evid(self):
        time.sleep(5)
        while True:
            try:
                self.ciclo_evidencias()
            except Exception as e:  # noqa: BLE001
                print(f"ciclo de evidencias: {e!r}", flush=True)
            time.sleep(30)

    # ---------- noticias, derivados y sentimiento (cada minuto) ----------
    def registrar_decision(self, simbolo, o, ahora):
        if self.registro:
            try:
                self.registro.guardar(simbolo, o, o["decision"], ahora)
                self.error_registro = None
            except (OSError, ValueError, sqlite3.Error) as error:
                self.error_registro = str(error)

    def velas_ref(self):
        v1m, v1h = {}, {}
        for s in COINS:
            v1m[s] = self.velas_1m(s)
            o = self.data.get(s) or {}
            v1h[s] = (o.get("_raw") or {}).get("1h")
        return v1m, v1h

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
                    if x.get("fuente"):
                        x["mercado"] = deriv_mercado(s, x["fuente"])
                self.deriv = d
                self.deriv_t = now_ms()
            self.registrar_derivados(d)
        except Exception:  # noqa: BLE001
            pass
        self.calc_sentimiento()

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
            fg = self.ctx.get("fg") if now_ms() - self.ctx.get("t_fg", 0) <= 36 * 3600000 else None
            ahora = now_ms()
            bl = [o.get("bL") for o in self.data.values() if o and o.get("bL") in ("AL", "NE", "BA")
                  and o.get("precio_t") and 0 <= ahora - o["precio_t"] <= FRESCO_MS]   # sin lecturas caducadas
            amp = statistics.mean(100 if b == "AL" else 50 if b == "NE" else 0 for b in bl) if bl else None
            crypto = list(CRIPTO)
            dv = {s: self.deriv_fresco(s) or {} for s in crypto}
            fund = [dv[s]["funding"] for s in crypto if dv[s].get("funding") is not None]
            lp = [dv[s]["largos_pct"] for s in crypto if dv[s].get("largos_pct") is not None]
            pm = self.pulso.get("MERCADO", {}).get("score") if self.pulso.get("MERCADO", {}).get("n_lectura", 1) else None
            pb = self.pulso.get("BTC", {}).get("score") if self.pulso.get("BTC", {}).get("n_lectura", 1) else None
            pulse = (pm if pb is None else pb if pm is None else (pm + pb) / 2) if (pm is not None or pb is not None) else None
            self.sent = compuesto(fg=float(fg["value"]) if fg else None, mc24=self.ctx.get("mc") if now_ms() - self.ctx.get("t_cg", 0) <= 900000 else None, amplitud=amp,
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
                        self.main(bool(self.data))
                    elif time.time() - self.last > 600:
                        self.main(True)
                    elif (not (self.flujo and self.flujo.conectado) or
                          any(o.get("price") and now_ms() - o.get("precio_t", o.get("_precio_t", 0)) > FRESCO_MS
                              for o in list(self.data.values()))):
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
                    if confirmado_rest(tk[key]):      # si el último negociado está fuera de la horquilla, sigue caducado
                        o["precio_t"] = o["t"]
                        o["precio_fuente"] = "REST"
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
        for f in (self._loop_main, self._loop_rt, self._loop_min, self._loop_evid):
            threading.Thread(target=f, daemon=True).start()

    # ---------- salidas para la web ----------
    def salud(self):
        fl = self.flujo.salud() if self.flujo else {"conectado": False}
        return {"registro_t": self.registro.ultimo_registro if self.registro else None, "registro_error": self.error_registro, "ws": fl, "rt_ms": self.rt_ms, "rt_t": self.rt_t, "noticias": {k: dict(v) for k, v in list(self.fund.estado.items())}, "cal_ok": self.fund.cal_ok,
                "historico": self.hist.resumen(), "evidencias_error": self.ev_error, "av": self.av_ultimo,
                "evid_eval_t": self.evid.ultima_eval if self.evid else None,
                "deriv_t": self.deriv_t, "deriv_ok": sum(1 for x in self.deriv.values() if x.get("ok")), "deriv_n": len(COINS),
                "last": int(self.last * 1000),
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
                  "sent": self.sent, "rnews": self.rnews, "calidad": self._calidad}
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
                coins[s] = {"decision": o.get("decision"), "precio_t": o.get("precio_t"), "precio_fuente": o.get("precio_fuente"), "price": o["price"], "chg": o.get("chg"), "z": zona_web(o.get("z")), "tm": o.get("tm"), "crit": o.get("crit"),
                            "conf": o.get("conf"), "tkv": o.get("tkv"),
                            "vivo": {k: v for k, v in vv.items() if k != "tf"} | {"tf": {t: {"score": x["score"], "rsi": x["rsi"], "v": x["v"], "cats": x["cats"]} for t, x in (vv.get("tf") or {}).items()}} if vv else None,
                            "micro": o.get("micro"), "prev": o.get("prev"),
                            "procedencia": o.get("procedencia"), "fuentes": o.get("fuentes")}
            out = {"v": self.version, "f": self.fast_v, "t": now_ms(), "coins": coins, "salud": self.salud(), "bar": self.bar,
                   "busy": self.busy, "calidad": self._calidad}
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
            for k in ("salud", "calidad"):
                js = json.dumps(out[k], separators=(",", ":"))
                if memo.get(k) == js:
                    del out[k]
                else:
                    memo[k] = js
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
