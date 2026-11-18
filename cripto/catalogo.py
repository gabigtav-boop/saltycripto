"""Catálogo de activos: la única lista de monedas del sistema.

Todo lo que trabaja «por moneda» sale de aquí: los pares de Kraken (REST y WebSocket), el histórico profundo (Binance y
Coinbase), los derivados (Kraken Futures), CoinGecko, las palabras de las noticias, la web (nombre, color, icono) y el
flujo de evidencias (emisión horaria, registro, seguimiento, evaluación y avisos trazables).
Para añadir una moneda basta con añadir una entrada: entra en todo el flujo sin mantener otra lista.

Campos:
  nombre, etiqueta, color, icono   presentación en la web (`etiqueta` solo si difiere del símbolo: ORO para PAXG)
  kraken                           `wsname` del par en la API REST de Kraken (XBT/EUR para BTC)
  ws                               nombre del par en el WebSocket v2 de Kraken (BTC/EUR)
  coingecko, binance, coinbase     identificadores en esas fuentes (Binance/Coinbase solo para el histórico profundo;
                                   si no hay par en euros se usa USDT/USD: solo se usan magnitudes relativas)
  futuros                          contrato perpetuo de Kraken Futures (derivados de contexto)
  cripto                           False para activos que no cuentan en los agregados del mercado cripto (oro)
  escala_mov                       factor de los umbrales de «movimiento brusco» (el oro se mueve la mitad)
  palabras                         expresiones regulares de las noticias (el ticker de palabras corrientes, solo en mayúsculas)
"""

CATALOGO = {
    "BTC": {"nombre": "Bitcoin", "color": "#f7931a", "icono": "₿", "kraken": "XBT/EUR", "ws": "BTC/EUR",
            "coingecko": "bitcoin", "binance": "BTCEUR", "coinbase": "BTC-EUR", "futuros": "PF_XBTUSD",
            "palabras": [r"bitcoin", r"btc", r"satoshi"]},
    "ETH": {"nombre": "Ethereum", "color": "#8c8cff", "icono": "Ξ", "kraken": "ETH/EUR", "ws": "ETH/EUR",
            "coingecko": "ethereum", "binance": "ETHEUR", "coinbase": "ETH-EUR", "futuros": "PF_ETHUSD",
            "palabras": [r"ethereum", r"ether", r"eth", r"vitalik"]},
    "XRP": {"nombre": "XRP", "color": "#a9bccf", "icono": "X", "kraken": "XRP/EUR", "ws": "XRP/EUR",
            "coingecko": "ripple", "binance": "XRPEUR", "coinbase": "XRP-EUR", "futuros": "PF_XRPUSD",
            "palabras": [r"xrp", r"ripple"]},
    "SOL": {"nombre": "Solana", "color": "#b678ff", "icono": "S", "kraken": "SOL/EUR", "ws": "SOL/EUR",
            "coingecko": "solana", "binance": "SOLEUR", "coinbase": "SOL-EUR", "futuros": "PF_SOLUSD",
            "palabras": [r"solana", r"(?-i:\$?SOL)"]},          # «sol» (el astro) es una palabra corriente
    "HYPE": {"nombre": "Hyperliquid", "color": "#2ee6c9", "icono": "H", "kraken": "HYPE/EUR", "ws": "HYPE/EUR",
             "coingecko": "hyperliquid", "binance": "HYPEUSDT", "coinbase": "HYPE-USD", "futuros": "PF_HYPEUSD",
             "palabras": [r"hyperliquid", r"(?-i:\$?HYPE)"]},  # «hype» (exageración) también
    "SUI": {"nombre": "Sui", "color": "#4da2ff", "icono": "S", "kraken": "SUI/EUR", "ws": "SUI/EUR",
            "coingecko": "sui", "binance": "SUIEUR", "coinbase": "SUI-USD", "futuros": "PF_SUIUSD",
            "palabras": [r"(?-i:\$?SUI|Sui)"]},
    "ZEC": {"nombre": "Zcash", "color": "#f4b728", "icono": "Z", "kraken": "ZEC/EUR", "ws": "ZEC/EUR",
            "coingecko": "zcash", "binance": "ZECUSDT", "coinbase": "ZEC-USD", "futuros": "PF_ZECUSD",
            "palabras": [r"zcash", r"zec"]},
    "PAXG": {"nombre": "Oro (PAXG)", "etiqueta": "ORO", "color": "#d4a634", "icono": "Au", "kraken": "PAXG/EUR",
             "ws": "PAXG/EUR", "coingecko": "pax-gold", "binance": "PAXGUSDT", "coinbase": "PAXG-USD",
             "futuros": "PF_PAXGUSD", "cripto": False, "escala_mov": .5,
             "palabras": [r"gold", r"oro", r"paxg", r"bullion", r"xau"]},
}

SIMBOLOS = tuple(CATALOGO)
CRIPTO = tuple(s for s, a in CATALOGO.items() if a.get("cripto", True))


def campo(nombre):
    """{símbolo: valor} de un campo para las monedas que lo tienen."""
    return {s: a[nombre] for s, a in CATALOGO.items() if a.get(nombre)}


def par(s):
    """Par del mercado spot analizado (nombre del WebSocket v2 de Kraken)."""
    return CATALOGO[s]["ws"] if s in CATALOGO else f"{s}/EUR"


def web():
    """Lo que la página necesita de cada moneda (se sirve como /catalogo.js antes del código de la página)."""
    out = {}
    for s, a in CATALOGO.items():
        x = {"n": a["nombre"], "a": a["color"], "i": a["icono"]}
        if a.get("etiqueta"):
            x["lab"] = a["etiqueta"]
        if not a.get("cripto", True):
            x["cr"] = False
        if a.get("escala_mov"):
            x["mv"] = a["escala_mov"]
        out[s] = x
    return out
