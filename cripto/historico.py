"""Histórico profundo para calibrar el rango probable.

Kraken solo devuelve 720 velas por temporalidad (30 días en 1 h), muy poco para medir con qué frecuencia
se repite un movimiento. Aquí se descargan años de velas de Binance (y Coinbase como respaldo), se guardan
en disco (datos/historico/) y se actualizan de forma incremental. Las velas antiguas se empalman con las de
Kraken ajustando la escala en el punto de unión (como hacen los proveedores de datos al "rellenar" series),
de modo que la serie resultante es continua y los cálculos (todos ellos relativos: retornos, distancias en
ATR, ratios) no dependen del mercado de origen.

Alineación: Binance y Kraken cierran las velas de 1 m, 1 h, 4 h y 1 d en los mismos instantes (UTC).
La semanal no se rellena: Kraken ya ofrece más de 10 años y su semana empieza en jueves."""
import json
import os
import pathlib
import threading
import time

from . import fuentes

DIR = pathlib.Path(__file__).resolve().parent.parent / "datos" / "historico"
BINANCE = "https://api.binance.com/api/v3/klines"
COINBASE = "https://api.exchange.coinbase.com/products/{}/candles"
# pares por moneda: primero en euros; si no existe, en USDT (solo se usan magnitudes relativas)
BIN_SYM = {"BTC": "BTCEUR", "ETH": "ETHEUR", "XRP": "XRPEUR", "SOL": "SOLEUR", "SUI": "SUIEUR",
           "HYPE": "HYPEUSDT", "ZEC": "ZECUSDT", "PAXG": "PAXGUSDT"}
CB_SYM = {"BTC": "BTC-EUR", "ETH": "ETH-EUR", "XRP": "XRP-EUR", "SOL": "SOL-EUR", "HYPE": "HYPE-USD",
          "SUI": "SUI-USD", "ZEC": "ZEC-USD", "PAXG": "PAXG-USD"}
SIN_BINANCE = os.environ.get("SALTY_SIN_BINANCE") == "1"
TF_MS = {"1m": 60000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
BIN_INT = {"1m": "1m", "1h": "1h", "4h": "4h", "1d": "1d"}
CB_GRAN = {"1m": 60, "1h": 3600, "1d": 86400}        # Coinbase no tiene 4 h: se agrega desde 1 h
OBJETIVO = {"1m": 3000, "1h": 4000, "4h": 3000, "1d": 1600}   # velas que se intentan tener


def agregar(rows, factor):
    """Agrupa velas consecutivas de n en n (alineadas al múltiplo del periodo)."""
    if not rows:
        return []
    ms = rows[1]["t"] - rows[0]["t"] if len(rows) > 1 else 0
    if ms <= 0:
        return []
    per = ms * factor
    out = {}
    for r in rows:
        k = r["t"] - r["t"] % per
        g = out.get(k)
        if not g:
            out[k] = dict(r, t=k)
        else:
            g["h"] = max(g["h"], r["h"])
            g["l"] = min(g["l"], r["l"])
            g["c"] = r["c"]
            g["v"] += r["v"]
    return [out[k] for k in sorted(out)]


def _binance(sym, tf, n, desde=None):
    out = []
    end = None
    while len(out) < n:
        q = f"{BINANCE}?symbol={sym}&interval={BIN_INT[tf]}&limit=1000"
        if end is not None:
            q += f"&endTime={end}"
        if desde is not None:
            q += f"&startTime={desde}"
        d = fuentes.jget(q, 12)
        if not d:
            break
        rows = [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in d]
        out = rows + out
        if len(d) < 1000 or desde is not None:
            break
        end = rows[0]["t"] - 1
        time.sleep(.12)
    return out


def _coinbase(sym, tf, n, desde=None):
    base = "1h" if tf == "4h" else tf
    gran = CB_GRAN[base]
    need = n * (4 if tf == "4h" else 1)
    out = []
    end = int(time.time())
    while len(out) < need:
        start = max(end - 300 * gran, (desde // 1000) if desde else 0)
        u = COINBASE.format(sym) + f"?granularity={gran}&start={start}&end={end}"
        d = fuentes.jget(u, 12)
        if not d:
            break
        rows = fuentes.parse_coinbase(d)
        out = rows + out
        if desde is not None and start <= desde // 1000:
            break
        end = start
        time.sleep(.2)
    return agregar(out, 4) if tf == "4h" else out


def empalmar(profundo, kraken):
    """Velas antiguas (otro mercado) + velas de Kraken, con la escala ajustada en la unión.
    Devuelve una lista continua ordenada por tiempo; las de Kraken mandan en el tramo que cubren."""
    if not kraken:
        return list(profundo)
    if not profundo:
        return list(kraken)
    t0 = kraken[0]["t"]
    kt = {r["t"]: r for r in kraken}
    comunes = [(kt[r["t"]], r) for r in profundo if r["t"] in kt][:48]
    if comunes:
        ratio = sum(k["c"] for k, _ in comunes) / sum(p["c"] for _, p in comunes)
        pv = sum(p["v"] for _, p in comunes)
        vratio = sum(k["v"] for k, _ in comunes) / pv if pv > 0 else 1.0
    else:
        ratio = kraken[0]["o"] / profundo[-1]["c"]
        vratio = 1.0
    if not (0.2 < ratio < 5):       # mercados incompatibles (par mal elegido): no se empalma
        return list(kraken)
    viejas = [{"t": r["t"], "o": r["o"] * ratio, "h": r["h"] * ratio, "l": r["l"] * ratio, "c": r["c"] * ratio,
               "v": r["v"] * vratio} for r in profundo if r["t"] < t0]
    return viejas + list(kraken)


class Historico:
    def __init__(self, directorio=DIR, net=True):
        self.dir = pathlib.Path(directorio)
        self.net = net
        self.lock = threading.Lock()
        self.mem = {}
        self.estado = {}        # (sym, tf) -> {fuente, n, t, error}
        self._caido = {}        # fuente -> hasta cuándo no se intenta
        self._cerrojos = {}     # (moneda, plazo) -> cerrojo: dos hilos no descargan ni escriben el mismo archivo a la vez
        self._cerrojo_de_cerrojos = threading.Lock()

    def _cerrojo(self, sym, tf):
        with self._cerrojo_de_cerrojos:
            return self._cerrojos.setdefault((sym, tf), threading.RLock())

    def _path(self, sym, tf):
        return self.dir / f"{sym}_{tf}.json"

    def cargar(self, sym, tf):
        k = (sym, tf)
        if k in self.mem:
            return self.mem[k]
        try:
            d = json.loads(self._path(sym, tf).read_text())
            rows = [{"t": x[0], "o": x[1], "h": x[2], "l": x[3], "c": x[4], "v": x[5]} for x in d["rows"]]
            self.mem[k] = {"fuente": d.get("fuente"), "rows": rows, "tope": bool(d.get("tope"))}
            if rows:
                self.estado[k] = {"fuente": d.get("fuente"), "n": len(rows), "t": rows[-1]["t"], "error": None}
        except Exception:  # noqa: BLE001
            self.mem[k] = {"fuente": None, "rows": []}
        return self.mem[k]

    def _guardar(self, sym, tf, h):
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            ruta = self._path(sym, tf)
            tmp = ruta.with_suffix(".tmp")      # escritura atómica: un corte a mitad no deja un archivo roto
            tmp.write_text(json.dumps({"fuente": h["fuente"], "tope": bool(h.get("tope")),
                                       "rows": [[r["t"], r["o"], r["h"], r["l"], r["c"], r["v"]] for r in h["rows"]]}))
            tmp.replace(ruta)
        except Exception:  # noqa: BLE001
            pass

    def _descargar(self, sym, tf, n, desde):
        for fuente, fn, tabla in (("binance", _binance, BIN_SYM), ("coinbase", _coinbase, CB_SYM)):
            if time.time() < self._caido.get(fuente, 0) or sym not in tabla or (fuente == "binance" and SIN_BINANCE):
                continue
            try:
                rows = fn(tabla[sym], tf, n, desde)
                if rows:
                    return fuente + ":" + tabla[sym], rows
            except Exception as e:  # noqa: BLE001
                self._caido[fuente] = time.time() + 600
                self.estado[(sym, tf)] = {"error": str(e)[:80]}
        return None, []

    def actualizar(self, sym, tf, ahora_ms=None):
        """Completa el histórico de la moneda/temporalidad (descarga inicial o solo las velas nuevas)."""
        with self._cerrojo(sym, tf):
            return self._actualizar(sym, tf, ahora_ms)

    def _actualizar(self, sym, tf, ahora_ms=None):
        if tf not in TF_MS or not self.net:
            return self.cargar(sym, tf)["rows"]
        h = self.cargar(sym, tf)
        ahora = ahora_ms or int(time.time() * 1000)
        rows = h["rows"]
        objetivo = OBJETIVO[tf]
        completo = len(rows) >= objetivo * .9 or h.get("tope")     # "tope": el mercado no tiene más historia (monedas nuevas)
        if rows and ahora - rows[-1]["t"] < 2 * TF_MS[tf] and completo:
            return rows
        if rows and completo:
            fuente, nuevas = self._descargar(sym, tf, 1000, rows[-1]["t"] + 1)
            if fuente and fuente != h["fuente"]:
                # cambió el mercado de origen (p. ej. Binance no responde y se usa Coinbase): no se mezclan
                # mercados en una misma serie; se rehace entera con la fuente nueva
                fuente, nuevas = self._descargar(sym, tf, objetivo, None)
                if nuevas:
                    rows = []
                    h["tope"] = len(nuevas) < objetivo * .9
        else:
            fuente, nuevas = self._descargar(sym, tf, objetivo, None)
            if nuevas:
                rows = []
                h["tope"] = len(nuevas) < objetivo * .9
        if nuevas:
            vistos = {r["t"] for r in rows}
            rows = rows + [r for r in nuevas if r["t"] not in vistos]
            rows.sort(key=lambda r: r["t"])
            rows = rows[-(objetivo + 200):]
            # la última vela puede estar en formación: se descarta si su periodo no ha cerrado
            if rows and rows[-1]["t"] + TF_MS[tf] > ahora:
                rows = rows[:-1]
            with self.lock:
                h["rows"], h["fuente"] = rows, fuente or h["fuente"]
                self.estado[(sym, tf)] = {"fuente": h["fuente"], "n": len(rows), "t": ahora, "error": None}
            self._guardar(sym, tf, h)
        return h["rows"]

    def serie(self, sym, tf, kraken):
        """Serie continua para calibrar: histórico profundo empalmado con las velas de Kraken."""
        return empalmar(self.cargar(sym, tf)["rows"], kraken)

    def resumen(self):
        out = {}
        for (s, tf), e in list(self.estado.items()):      # otros hilos añaden entradas mientras tanto
            out[f"{s} {tf}"] = e
        return out
