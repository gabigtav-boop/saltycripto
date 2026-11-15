import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import numpy as np, pandas as pd
import indicators as ind
import demo


def df():
    return demo.candles("BTC", 60, 400)


def test_ema_rsi_atr_vs_ta():
    ta = __import__("ta")
    d = df()
    ref = ta.trend.EMAIndicator(d.close, 20).ema_indicator()
    assert np.allclose(ind.ema(d.close, 20)[40:], ref[40:], rtol=1e-3)
    ref = ta.momentum.RSIIndicator(d.close, 14).rsi()
    assert np.allclose(ind.rsi(d.close)[150:], ref[150:], atol=0.05)
    ref = ta.volatility.AverageTrueRange(d.high, d.low, d.close, 14).average_true_range()
    assert np.allclose(ind.atr(d)[100:], ref[100:], rtol=0.02)


def test_rsi_bounds():
    r = ind.rsi(df().close).dropna()
    assert ((r >= 0) & (r <= 100)).all()
