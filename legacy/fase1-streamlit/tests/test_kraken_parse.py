import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import kraken


def test_parse_ohlc():
    res = {"XXBTZEUR": [[1700000000, "70000.0", "70500", "69900", "70100", "70200", "1.5", 10],
                        [1700003600, "70100", "70600", "70000", "70300", "70250", "2.0", 12]], "last": 1700003600}
    d = kraken.parse_ohlc(res)
    assert list(d.columns) == ["time", "open", "high", "low", "close", "vwap", "volume", "count"]
    assert len(d) == 2 and d.close.iloc[-1] == 70300.0


def test_parse_ticker_maps_names():
    res = {"XXBTZEUR": {"c": ["70000", "1"], "b": ["69999", "1", "1"], "a": ["70001", "1", "1"], "o": "68000"}}
    out = kraken.parse_ticker(res, {"XBTEUR": "BTC"})
    assert out["BTC"]["last"] == 70000.0 and round(out["BTC"]["chg"], 2) == 2.94
