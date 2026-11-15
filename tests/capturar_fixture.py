"""Guarda una instantánea real del mercado (Kraken, Coinbase, CoinGecko) para las pruebas de paridad.
Uso: .venv/bin/python -m tests.capturar_fixture"""
import json
import pathlib
import time

from cripto import fuentes
from cripto.servicio import CGID, COINS

OUT = pathlib.Path(__file__).with_name("fixtures") / "mercado.json"


def main():
    k = fuentes.Kraken()
    ap = k.get("AssetPairs")
    pairs = {s: next((kk for kk, v in ap.items() if v.get("wsname") == ws), None) for s, ws in COINS.items()}
    keys = [p for p in pairs.values() if p]
    fx = {"now": int(time.time() * 1000), "pairs": pairs, "ticker": k.get("Ticker?pair=" + ",".join(keys)),
          "depth": {}, "ohlc": {}, "cbp": {}, "cbc": {}, "mk": None}
    for s, key in pairs.items():
        if not key:
            continue
        fx["depth"][key] = k.get(f"Depth?pair={key}&count=500")
        for m in (60, 240, 1440, 10080):
            fx["ohlc"][f"{key}|{m}"] = k.get(f"OHLC?pair={key}&interval={m}")
        try:
            fx["cbp"][s] = fuentes.jget(f"https://api.coinbase.com/v2/prices/{s}-EUR/spot")
        except Exception:  # noqa: BLE001
            pass
        try:
            fx["cbc"][s] = fuentes.jget(f"https://api.exchange.coinbase.com/products/{s}-EUR/candles?granularity=86400")
        except Exception:  # noqa: BLE001
            pass
    fx["mk"] = fuentes.jget("https://api.coingecko.com/api/v3/coins/markets?vs_currency=eur&price_change_percentage=24h&ids=" + ",".join(CGID.values()))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(fx))
    print("guardado", OUT, round(OUT.stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    main()
