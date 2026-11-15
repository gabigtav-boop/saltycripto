"""Configuración. Aquí se añaden o quitan monedas."""
import os

DEMO = os.environ.get("DEMO") == "1"   # DEMO=1 -> datos simulados, sin internet

# nombre mostrado: (símbolo WebSocket v2, par REST de Kraken)
COINS = {
    "BTC":  ("BTC/EUR",  "XBTEUR"),
    "ETH":  ("ETH/EUR",  "ETHEUR"),
    "XRP":  ("XRP/EUR",  "XRPEUR"),
    "SOL":  ("SOL/EUR",  "SOLEUR"),
    "HYPE": ("HYPE/EUR", "HYPEEUR"),
    "SUI":  ("SUI/EUR",  "SUIEUR"),
    "ZEC":  ("ZEC/EUR",  "ZECEUR"),
    "ORO":  ("PAXG/EUR", "PAXGEUR"),   # oro tokenizado (PAXG)
}

# etiqueta -> minutos de vela en Kraken
TIMEFRAMES = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

WS_URL = "wss://ws.kraken.com/v2"
REST = "https://api.kraken.com/0/public"
STALE_SECONDS = 15      # si el WebSocket no da precio en 15 s, se usa REST
