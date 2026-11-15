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
        self.t = 0

    def sorted(self):
        a = sorted(self.asks.items())[:BOOK_DEPTH]
        b = sorted(self.bids.items(), reverse=True)[:BOOK_DEPTH]
        return a, b

    def apply(self, d, snapshot):
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
            return True
        good = book_checksum(a, b, self.pdec, self.qdec) == cs
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
                self.error = None
                espera = 1
                while not self._stop:
                    raw = ws.recv()
                    if not raw:
                        raise ConnectionError("conexión cerrada")
                    self._msg(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:120]
            self.conectado = False
            self.on_update()
            if self._stop:
                break
            self.reconexiones += 1
            time.sleep(espera)
            espera = min(30, espera * 2)

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
                    self.trades[s].append({"p": d["price"], "q": d["qty"], "side": d["side"], "t": ts, "id": d.get("trade_id")})
                    if typ == "update":
                        self.latencias.append(now * 1000 - ts)
                elif ch == "book":
                    if not self.libros[s].apply(d, typ == "snapshot"):
                        self._resub_book(s)
                elif ch == "ohlc":
                    t0 = iso_ms(d["interval_begin"])
                    self.velas1m[s][t0] = {"t": t0, "o": d["open"], "h": d["high"], "l": d["low"], "c": d["close"], "v": d["volume"]}
                    if len(self.velas1m[s]) > 1500:
                        for k in sorted(self.velas1m[s])[:-1440]:
                            del self.velas1m[s][k]
        self.on_update()

    # ---------- lecturas ----------
    def precio(self, s, max_age=6 * 3600):
        """Último precio negociado. Kraken solo envía ticker cuando hay operaciones: en pares poco
        líquidos (oro) puede tener minutos y sigue siendo el precio real mientras la conexión esté viva."""
        t = self.ticker.get(s)
        if self.conectado and t and time.time() - t["t"] <= max_age and t.get("last"):
            return float(t["last"])
        return None

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
        with self.lock:
            a, b = self.libros[s].sorted()
            return {"asks": a[:n], "bids": b[:n], "ok": self.libros[s].ok, "bad": self.libros[s].bad}

    def ultimos_trades(self, s, n=40):
        with self.lock:
            return list(self.trades[s])[-n:][::-1]

    def micro(self, s):
        """Lectura de microestructura: flujo de órdenes, presión del libro y momento en minutos."""
        now = time.time() * 1000
        with self.lock:
            tr = list(self.trades[s])
            a, b = self.libros[s].sorted()
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
        if vel:
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
