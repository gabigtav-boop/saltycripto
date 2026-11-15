"""Fuentes de datos públicas (sin claves): Kraken, Coinbase, CoinGecko y alternative.me.
Las llamadas a Kraken van en cola (una a la vez, 450 ms entre ellas) para respetar su límite."""
import threading
import time

import requests

KRAKEN = "https://api.kraken.com/0/public/"
UA = {"User-Agent": "saltycripto/1"}
_session = requests.Session()
_session.headers.update(UA)


class KrakenError(Exception):
    pass


class Kraken:
    def __init__(self):
        self._q = threading.Lock()

    def _get(self, path):
        for i in range(3):
            try:
                r = _session.get(KRAKEN + path, timeout=15)
                if not r.ok:
                    raise KrakenError(f"HTTP {r.status_code}")
                d = r.json()
                err = d.get("error") or []
                if err:
                    if "many" in err[0].lower() or "limit" in err[0].lower():
                        time.sleep(2.5)
                        continue
                    raise KrakenError(err[0])
                time.sleep(.45)
                return d["result"]
            except Exception as e:  # noqa: BLE001
                if i == 2:
                    raise KrakenError(str(e)) from e
                time.sleep(1.5)
        raise KrakenError("límite de peticiones de Kraken")

    def get(self, path):
        with self._q:
            return self._get(path)


def jget(url, timeout=12):
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def parse_ohlc(result):
    k = next(x for x in result if x != "last")
    return [{"t": int(x[0]) * 1000, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[6])}
            for x in result[k]]


def parse_coinbase(d):
    rows = [{"t": int(x[0]) * 1000, "l": float(x[1]), "h": float(x[2]), "o": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in d]
    rows.sort(key=lambda r: r["t"])
    return rows
