"""Flujo en vivo de Kraken (WebSocket v2): ticker, libro de órdenes con verificación CRC32,
operaciones y velas de 1 minuto. Reconexión automática."""
import collections
import json
import ssl
import threading
import time
import zlib
from datetime import datetime

import certifi
import websocket

WS_URL = "wss://ws.kraken.com/v2"
BOOK_DEPTH = 25


def iso_ms(s):
    """'2026-11-15T03:05:28.318738902Z' -> milisegundos epoch."""
    s = s.rstrip("Z")
    if "." in s:
        a, b = s.split(".")
        s = a + "." + b[:6]
    return int(datetime.fromisoformat(s + "+00:00").timestamp() * 1000)


def _fmt(x, dec):
    return f"{x:.{dec}f}".replace(".", "").lstrip("0")


def book_checksum(asks, bids, pdec, qdec):
    """CRC32 de Kraken v2: 10 mejores asks (de menor a mayor) y 10 mejores bids (de mayor a menor)."""
    s = "".join(_fmt(p, pdec) + _fmt(q, qdec) for p, q in asks[:10])
    s += "".join(_fmt(p, pdec) + _fmt(q, qdec) for p, q in bids[:10])
    return zlib.crc32(s.encode()) & 0xFFFFFFFF


class Libro:
    def __init__(self, pdec, qdec):
        self.bids = {}
        self.asks = {}
        self.pdec, self.qdec = pdec, qdec
        self.ok = 0
        self.bad = 0
        self.valido = False
        self.t = 0

    def sorted(self):
        a = sorted(self.asks.items())[:BOOK_DEPTH]
        b = sorted(self.bids.items(), reverse=True)[:BOOK_DEPTH]
        return a, b

    def apply(self, d, snapshot):
        if not snapshot and not self.valido:
            return False
        if snapshot:
            self.bids.clear()
            self.asks.clear()
        for side, book in (("bids", self.bids), ("asks", self.asks)):
            for lv in d.get(side, []):
                if lv["qty"] == 0:
                    book.pop(lv["price"], None)
                else:
                    book[lv["price"]] = lv["qty"]
        a, b = self.sorted()
        # truncar a la profundidad suscrita, como exige Kraken
        self.asks = dict(a)
        self.bids = dict(b)
        self.t = time.time()
        cs = d.get("checksum")
        if cs is None:
            self.valido = True
            return True
        good = book_checksum(a, b, self.pdec, self.qdec) == cs
        self.valido = good
        if good:
            self.ok += 1
        else:
            self.bad += 1
        return good


class Flujo:
    """Mantiene en memoria el estado en vivo de todos los pares."""

    def __init__(self, simbolos, precisiones, on_update=None):
        self.simbolos = list(simbolos)          # ['BTC/EUR', ...]
        self.prec = precisiones                 # {'BTC/EUR': (pdec, qdec)}
        self.on_update = on_update or (lambda: None)
        self.lock = threading.Lock()
        self.ticker = {}
        self.libros = {s: Libro(*self.prec.get(s, (8, 8))) for s in self.simbolos}
        self.trades = {s: collections.deque(maxlen=400) for s in self.simbolos}
        self.velas1m = {s: {} for s in self.simbolos}   # inicio_ms -> vela
        self.conectado = False
        self.ultimo = 0
        self.mensajes = collections.deque(maxlen=50000)
        self.reconexiones = 0
        self.latencias = collections.deque(maxlen=200)
        self.error = None
        self._ws = None
        self._stop = False
        # procedencia y continuidad (evidencias): qué se recibió, cuándo, y durante qué tramos el canal estuvo activo
        self.ultima_op = {}                                   # s -> última operación recibida {p, q, side, t (bolsa), recibido}
        self.ohlc_desde = {}                                  # s -> ms desde que el canal de velas está activo en esta conexión
        self.trade_desde = {}                                 # s -> ms desde que el canal de operaciones está activo
        self.segmentos = {s: collections.deque(maxlen=500) for s in self.simbolos}   # tramos cerrados (desde, hasta) del canal de velas
        self.pend_ws = {s: {} for s in self.simbolos}         # velas de 1 min recibidas por WebSocket aún sin guardar
        self.agregados = {s: {} for s in self.simbolos}       # minuto -> volumen negociado por lado (solo operaciones en vivo)
        self.conectado_t = 0

    # ---------- semilla REST de velas de 1 minuto ----------
    def sembrar_1m(self, s, rows):
        with self.lock:
            v = self.velas1m[s]
            for r in rows:
                v.setdefault(r["t"], dict(r))

    @staticmethod
    def _rellenar(v):
        """Velas de 1 minuto continuas: Kraken solo envía velas cuando hay operaciones, así que un minuto sin
        operaciones se rellena con una vela plana al último precio y volumen 0 (práctica estándar)."""
        out = []
        for k in sorted(v):
            r = v[k]
            if out:
                prev = out[-1]
                t = prev["t"] + 60000
                while t < r["t"] and len(out) < 5000:
                    c = prev["c"]
                    out.append({"t": t, "o": c, "h": c, "l": c, "c": c, "v": 0.0})
                    t += 60000
            out.append(dict(r))
        return out

    def velas(self, s):
        with self.lock:
            return self._rellenar(self.velas1m[s])

    # ---------- conexión ----------
    def start(self):
        threading.Thread(target=self._run, daemon=True, name="kraken-ws").start()

    def stop(self):
        self._stop = True
        try:
            self._ws and self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    def _run(self):
        espera = 1
        while not self._stop:
            try:
                ws = websocket.create_connection(WS_URL, timeout=15, sslopt={"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()})
                self._ws = ws
                for ch, extra in (("ticker", {}), ("trade", {"snapshot": True}), ("book", {"depth": BOOK_DEPTH}), ("ohlc", {"interval": 1})):
                    ws.send(json.dumps({"method": "subscribe", "params": {"channel": ch, "symbol": self.simbolos, **extra}}))
                self.conectado = True
                self.conectado_t = time.time()
                self.error = None
                espera = 1
                while not self._stop:
                    raw = ws.recv()
                    if not raw:
                        raise ConnectionError("conexión cerrada")
                    self._msg(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:120]
            self._desconectado()
            self.on_update()
            if self._stop:
                break
            self.reconexiones += 1
            time.sleep(espera)
            espera = min(30, espera * 2)

    def _desconectado(self):
        """Cierra los tramos de cobertura en el último mensaje recibido y vacía lo que deja de ser válido."""
        with self.lock:
            self.conectado = False
            fin = int(self.ultimo * 1000)
            for s, desde in self.ohlc_desde.items():
                if fin > desde:
                    self.segmentos[s].append((desde, fin))
            self.ohlc_desde.clear()
            self.trade_desde.clear()
            for cola in self.agregados.values():     # un minuto cortado por la desconexión no está completo
                for m, g in cola.items():
                    if m + 60000 > fin:
                        g["completo"] = False
            self.ticker.clear()
            for libro in self.libros.values():
                libro.valido = False
            for operaciones in self.trades.values():
                operaciones.clear()

    def _resub_book(self, s):
        try:
            self._ws.send(json.dumps({"method": "unsubscribe", "params": {"channel": "book", "symbol": [s], "depth": BOOK_DEPTH}}))
            self._ws.send(json.dumps({"method": "subscribe", "params": {"channel": "book", "symbol": [s], "depth": BOOK_DEPTH}}))
        except Exception:  # noqa: BLE001
            pass

    def _msg(self, m):
        now = time.time()
        self.ultimo = now
        with self.lock:
            self.mensajes.append(now)
        ch = m.get("channel")
        if ch not in ("ticker", "trade", "book", "ohlc"):
            return
        typ = m.get("type")
        with self.lock:
            for d in m.get("data", []):
                s = d.get("symbol")
                if s not in self.libros:
                    continue
                if ch == "ticker":
                    self.ticker[s] = dict(d, t=now)
                elif ch == "trade":
                    ts = iso_ms(d["timestamp"])
                    op = {"p": d["price"], "q": d["qty"], "side": d["side"], "t": ts, "id": d.get("trade_id")}
                    self.trades[s].append(op)
                    if ts >= (self.ultima_op.get(s) or {}).get("t", 0):
                        self.ultima_op[s] = dict(op, recibido=int(now * 1000))
                    if typ == "snapshot":
                        self.trade_desde.setdefault(s, int(now * 1000))
                    elif typ == "update":
                        self.latencias.append(now * 1000 - ts)
                        self.trade_desde.setdefault(s, int(now * 1000))
                        m = ts - ts % 60000
                        if m >= self.trade_desde[s] - self.trade_desde[s] % 60000:
                            g = self.agregados[s].setdefault(m, {"compra": 0.0, "venta": 0.0, "n": 0, "q": 0.0,
                                                                  "completo": m >= self.trade_desde[s]})
                            g["compra" if d["side"] == "buy" else "venta"] += d["price"] * d["qty"]
                            g["n"] += 1
                            g["q"] += d["qty"]
                elif ch == "book":
                    if not self.libros[s].apply(d, typ == "snapshot"):
                        self._resub_book(s)
                elif ch == "ohlc":
                    t0 = iso_ms(d["interval_begin"])
                    self.ohlc_desde.setdefault(s, int(now * 1000))
                    self.velas1m[s][t0] = {"t": t0, "o": d["open"], "h": d["high"], "l": d["low"], "c": d["close"], "v": d["volume"]}
                    self.pend_ws[s][t0] = dict(self.velas1m[s][t0], n=d.get("trades"))
                    if len(self.velas1m[s]) > 1500:
                        for k in sorted(self.velas1m[s])[:-1440]:
                            del self.velas1m[s][k]
                    for cola in (self.pend_ws[s], self.agregados[s]):   # sin nadie que las guarde, no crecen sin límite
                        if len(cola) > 1500:
                            for k in sorted(cola)[:-1440]:
                                del cola[k]
        self.on_update()

    # ---------- lecturas ----------
    def precio(self, s, max_age=6 * 3600):
        """Último precio negociado. Kraken solo envía ticker cuando hay operaciones: en pares poco
        líquidos (oro) puede tener minutos y sigue siendo el precio real mientras la conexión esté viva."""
        t = self.ticker.get(s)
        if self.conectado and t and time.time() - t["t"] <= max_age and t.get("last"):
            return float(t["last"])
        return None

    def confirmacion(self, s, precio, max_age=10):
        """Hora (s) en que el flujo confirma por última vez que `precio` sigue vigente, o None.
        Kraken solo envía ticker cuando hay operaciones: en un par tranquilo el último precio negociado
        sigue vigente mientras la conexión esté viva (último mensaje hace ≤ max_age s; Kraken envía cada
        cambio del libro y latidos) y el libro, con CRC correcto, lo contenga en su horquilla (±0,1 %).
        Si se corta la conexión, el libro falla o el precio se sale de la horquilla, no se confirma."""
        if not precio or precio <= 0:
            return None
        with self.lock:
            libro = self.libros.get(s)
            if not (self.conectado and libro and libro.valido and libro.asks and libro.bids
                    and 0 <= time.time() - self.ultimo <= max_age):
                return None
            ask, bid = min(libro.asks), max(libro.bids)
            t = self.ultimo
        return t if bid * (1 - .001) <= precio <= ask * (1 + .001) else None

    # ---------- evidencias: continuidad, velas cerradas, volumen y profundidad ----------
    def cobertura_ws(self, s, ahora_ms=None):
        """Tramos (desde, hasta) en ms durante los que el canal de velas de `s` estuvo activo, incluido el actual.
        Dentro de un tramo, un minuto sin vela es un minuto sin operaciones; fuera de todos, es un hueco de conexión."""
        ahora_ms = ahora_ms if ahora_ms is not None else int(time.time() * 1000)
        with self.lock:
            out = list(self.segmentos.get(s, ()))
            if self.conectado and s in self.ohlc_desde:
                out.append((self.ohlc_desde[s], min(ahora_ms, int(self.ultimo * 1000))))
        return out

    def tomar_cerradas(self, s, hasta_ms):
        """Saca (para guardarlas) las velas de 1 minuto recibidas por WebSocket que ya cerraron antes de `hasta_ms`."""
        with self.lock:
            cola = self.pend_ws.get(s, {})
            listas = sorted(t for t in cola if t + 60000 <= hasta_ms)
            return [cola.pop(t) for t in listas]

    def tomar_agregados(self, s, hasta_ms):
        """Volumen negociado por minuto y lado (operaciones recibidas en vivo) de los minutos ya cerrados."""
        with self.lock:
            cola = self.agregados.get(s, {})
            listos = sorted(m for m in cola if m + 60000 <= hasta_ms)
            return [dict(cola.pop(m), minuto=m) for m in listos]

    def profundidad(self, s, bandas=(.001, .005, .01)):
        """Foto del libro verificado: mejor compra y venta, horquilla y euros acumulados a cada distancia del precio medio.
        None si el libro no está verificado (CRC), la conexión cayó o la foto tiene más de 120 s."""
        with self.lock:
            libro = self.libros.get(s)
            if not (self.conectado and libro and libro.valido and libro.asks and libro.bids
                    and time.time() - libro.t <= 120):
                return None
            a, b = libro.sorted()
            t = libro.t
        bid, ask = b[0][0], a[0][0]
        md = (bid + ask) / 2
        # con BOOK_DEPTH niveles por lado, el libro solo llega hasta cierta distancia: una banda más allá quedaría truncada
        alc_c, alc_v = (1 - b[-1][0] / md) * 100, (a[-1][0] / md - 1) * 100
        out = {"t": int(t * 1000), "bid": bid, "ask": ask, "spread_pct": (ask - bid) / md * 100, "niveles": [len(b), len(a)],
               "alcance_compra_pct": alc_c, "alcance_venta_pct": alc_v}
        for x in bandas:
            k = f"{x * 100:g}".replace(".", ",")
            completo_c = len(b) < BOOK_DEPTH or alc_c >= x * 100
            completo_v = len(a) < BOOK_DEPTH or alc_v >= x * 100
            out[f"compra_{k}"] = sum(p * q for p, q in b if p >= md * (1 - x)) if completo_c else None
            out[f"venta_{k}"] = sum(p * q for p, q in a if p <= md * (1 + x)) if completo_v else None
        return out

    def salud(self):
        now = time.time()
        with self.lock:     # el hilo del WebSocket añade mientras tanto: sin cerrojo la cola cambia al recorrerla
            msgs = sum(1 for x in self.mensajes if now - x <= 10) / 10
            lat = sorted(self.latencias)
        ok = sum(b.ok for b in self.libros.values())
        bad = sum(b.bad for b in self.libros.values())
        return {"conectado": self.conectado, "msg_s": round(msgs, 1), "ultimo": int(self.ultimo * 1000),
                "latencia_ms": round(lat[len(lat) // 2]) if lat else None, "reconexiones": self.reconexiones,
                "checksum_ok": ok, "checksum_mal": bad, "error": self.error}

    def libro(self, s, n=14):
        now = time.time() * 1000
        with self.lock:
            libro = self.libros[s]
            a, b = libro.sorted() if self.conectado and libro.valido and now - libro.t * 1000 <= 120000 else ([], [])
            return {"asks": a[:n], "bids": b[:n], "ok": self.libros[s].ok, "bad": self.libros[s].bad}

    def ultimos_trades(self, s, n=40):
        with self.lock:
            return list(self.trades[s])[-n:][::-1]

    def micro(self, s):
        """Lectura de microestructura: flujo de órdenes, presión del libro y momento en minutos."""
        now = time.time() * 1000
        with self.lock:
            tr = list(self.trades[s])
            libro = self.libros[s]
            a, b = libro.sorted() if self.conectado and libro.valido and now - libro.t * 1000 <= 120000 else ([], [])
            vel = self._rellenar({k: self.velas1m[s][k] for k in sorted(self.velas1m[s])[-121:]})[-121:]
        out = {}
        for lab, ms in (("1m", 6e4), ("5m", 3e5), ("15m", 9e5)):
            buy = sum(t["p"] * t["q"] for t in tr if now - t["t"] <= ms and t["side"] == "buy")
            sell = sum(t["p"] * t["q"] for t in tr if now - t["t"] <= ms and t["side"] == "sell")
            out["flujo_" + lab] = {"compra": buy, "venta": sell, "pct": buy / (buy + sell) * 100 if buy + sell > 0 else None}
        imb = None
        if a and b:
            md = (a[0][0] + b[0][0]) / 2
            nb = sum(p * q for p, q in b if p >= md * .995)
            na = sum(p * q for p, q in a if p <= md * 1.005)
            imb = nb / (nb + na) * 100 if nb + na > 0 else None
            out["spread_pct"] = (a[0][0] - b[0][0]) / md * 100
        out["libro_pct"] = imb
        # muros cercanos: el nivel más grande de cada lado si es al menos 3 veces la mediana
        for lado, arr, k in (("compra", b, "muro_compra"), ("venta", a, "muro_venta")):
            if len(arr) >= 5:
                nots = sorted(p * q for p, q in arr)
                med = nots[len(nots) // 2]
                p, q = max(arr, key=lambda x: x[0] * x[1])
                if med > 0 and p * q >= 3 * med:
                    out[k] = {"precio": p, "eur": p * q, "pct": (p / ((a[0][0] + b[0][0]) / 2) - 1) * 100}
        if vel and 0 <= now - vel[-1]["t"] < 120000:
            last = vel[-1]["c"]
            for lab, k in (("1m", 1), ("5m", 5), ("15m", 15), ("60m", 60)):
                out["mom_" + lab] = (last / vel[-1 - k]["c"] - 1) * 100 if len(vel) > k else None
            rets = [vel[i]["c"] / vel[i - 1]["c"] - 1 for i in range(max(1, len(vel) - 60), len(vel))]
            if len(rets) > 10:
                m = sum(rets) / len(rets)
                out["vol_1h"] = (sum((r - m) ** 2 for r in rets) / len(rets)) ** .5 * 60 ** .5 * 100
        # puntuación -100..100 de muy corto plazo
        parts = []
        f5 = out["flujo_5m"]["pct"]
        if f5 is not None:
            parts.append(((f5 - 50) / 25, 1.2))
        if imb is not None:
            parts.append(((imb - 50) / 25, .8))
        m5, m15 = out.get("mom_5m"), out.get("mom_15m")
        if m5 is not None:
            parts.append((m5 / .5, 1))
        if m15 is not None:
            parts.append((m15 / 1, 1))
        if parts:
            sc = sum(max(-1, min(1, v)) * w for v, w in parts) / sum(w for _, w in parts) * 100
            out["score"] = sc
            out["lectura"] = "Presión compradora" if sc >= 25 else "Presión vendedora" if sc <= -25 else "Equilibrio"
        return out
