"""Datos de Kraken (API pública, sin claves): velas por REST y precio en directo por WebSocket."""
import json
import threading
import time

import pandas as pd
import requests

import config

_lock = threading.Lock()
_last_call = [0.0]


def _get(path: str, params: dict, retries: int = 3) -> dict:
    """GET con reintentos y pausa mínima entre llamadas (respeta el límite de Kraken)."""
    err = None
    for i in range(retries):
        with _lock:
            wait = 0.35 - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
        try:
            r = requests.get(f"{config.REST}/{path}", params=params, timeout=10)
            r.raise_for_status()
            j = r.json()
            if j.get("error"):
                raise RuntimeError(", ".join(j["error"]))
            return j["result"]
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"Kraken no responde: {err}")


def parse_ohlc(result: dict) -> pd.DataFrame:
    key = next(k for k in result if k != "last")
    df = pd.DataFrame(result[key], columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"])
    for c in ["open", "high", "low", "close", "vwap", "volume"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="s")
    return df.reset_index(drop=True)  # la última vela es la actual (en formación)


def fetch_ohlc(pair: str, minutes: int) -> pd.DataFrame:
    return parse_ohlc(_get("OHLC", {"pair": pair, "interval": minutes}))


def _norm(k: str) -> str:
    """Kraken devuelve a veces XXBTZEUR para el par XBTEUR."""
    return k[1:4] + k[5:] if len(k) == 8 and k[0] == "X" and k[4] == "Z" else k


def parse_ticker(result: dict, rest_to_name: dict) -> dict:
    """Respuesta REST Ticker -> {moneda: {last,bid,ask,chg,ts}}."""
    out = {}
    for k, v in result.items():
        name = rest_to_name.get(k) or rest_to_name.get(_norm(k))
        if not name:
            continue
        last, op = float(v["c"][0]), float(v["o"])
        out[name] = {"last": last, "bid": float(v["b"][0]), "ask": float(v["a"][0]),
                     "chg": (last / op - 1) * 100 if op else 0.0, "ts": time.time()}
    return out


class LiveFeed:
    """Precio en directo: WebSocket v2 + respaldo por REST si el WS se queda mudo."""

    def __init__(self, coins: dict):
        self.coins = coins
        self.sym_to_name = {v[0]: k for k, v in coins.items()}
        self.rest_to_name = {v[1]: k for k, v in coins.items()}
        self.data: dict = {}
        self.status = "conectando…"
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run_ws, daemon=True).start()
        threading.Thread(target=self._poll, daemon=True).start()

    # --- WebSocket ---
    def _run_ws(self):
        import websocket  # websocket-client
        while True:
            try:
                ws = websocket.WebSocketApp(config.WS_URL, on_open=self._on_open, on_message=self._on_msg,
                                            on_error=lambda w, e: None)
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self.status = "WebSocket caído, reconectando…"
            time.sleep(3)

    def _on_open(self, ws):
        ws.send(json.dumps({"method": "subscribe",
                            "params": {"channel": "ticker", "symbol": list(self.sym_to_name), "event_trigger": "trades"}}))

    def _on_msg(self, ws, raw):
        try:
            m = json.loads(raw)
        except ValueError:
            return
        if m.get("channel") != "ticker" or "data" not in m:
            return
        for d in m["data"]:
            n = self.sym_to_name.get(d.get("symbol"))
            if n and d.get("last"):
                cur = self.data.get(n, {})
                cur.update({"last": float(d["last"]), "bid": float(d.get("bid", d["last"])),
                            "ask": float(d.get("ask", d["last"])), "ts": time.time(),
                            "chg": float(d.get("change_pct", cur.get("chg", 0.0)))})
                self.data[n] = cur
        self.status = "en directo (WebSocket)"

    # --- Respaldo REST ---
    def _poll(self):
        while True:
            time.sleep(5)
            fresh = [v for v in self.data.values() if time.time() - v["ts"] < config.STALE_SECONDS]
            if len(fresh) >= len(self.coins):
                continue
            try:
                res = _get("Ticker", {"pair": ",".join(v[1] for v in self.coins.values())})
                for n, v in parse_ticker(res, self.rest_to_name).items():
                    if n not in self.data or time.time() - self.data[n]["ts"] >= config.STALE_SECONDS:
                        self.data[n] = v
                self.status = "respaldo REST (cada 5 s)"
            except Exception as e:  # noqa: BLE001
                self.status = f"sin conexión con Kraken: {e}"

    def snapshot(self) -> dict:
        return dict(self.data)
