"""Datos simulados para probar sin internet (DEMO=1)."""
import time
import numpy as np
import pandas as pd

BASE = {"BTC": 80000, "ETH": 2800, "XRP": 2.1, "SOL": 140, "HYPE": 35, "SUI": 2.4, "ZEC": 300, "ORO": 3600}


def candles(name: str, minutes: int, n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash((name, minutes))) % 2**32)
    vol = 0.004 * (minutes / 60) ** 0.5
    ret = rng.normal(0, vol, n)
    close = BASE[name] * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    end = int(time.time() // (minutes * 60)) * minutes * 60
    t = pd.to_datetime(end - np.arange(n)[::-1] * minutes * 60, unit="s")
    return pd.DataFrame({"time": t, "open": open_, "high": hi, "low": lo, "close": close,
                         "vwap": close, "volume": rng.uniform(1, 100, n), "count": 1})


class DemoFeed:
    def __init__(self, coins):
        self.p = {c: float(BASE[c]) for c in coins}
        self.o = dict(self.p)
        self.status = "DEMO (datos simulados)"

    def start(self):
        pass

    def snapshot(self):
        rng = np.random.default_rng()
        out = {}
        for c in self.p:
            self.p[c] *= 1 + rng.normal(0, 0.0007)
            out[c] = {"last": self.p[c], "bid": self.p[c] * .9998, "ask": self.p[c] * 1.0002,
                      "chg": (self.p[c] / self.o[c] - 1) * 100, "ts": time.time()}
        return out
