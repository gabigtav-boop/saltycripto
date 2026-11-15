"""Verificación en vivo: compara lo que muestra Saltycripto con consultas independientes a las fuentes reales.
Uso (con la app en marcha):  .venv/bin/python verificar.py"""
import json
import sys
import time
import urllib.request

import requests

APP = "http://127.0.0.1:8502"
OK, KO = "\033[32m✓\033[0m", "\033[31m✗\033[0m"
fallos = 0


def check(cond, txt, det=""):
    global fallos
    if not cond:
        fallos += 1
    print(f"  {OK if cond else KO} {txt}" + (f"  \033[90m{det}\033[0m" if det else ""))


def tick():
    with urllib.request.urlopen(APP + "/api/stream", timeout=10) as r:
        for line in r:
            if line.startswith(b"data: "):
                return json.loads(line[6:])


def main():
    try:
        st = requests.get(APP + "/api/state", timeout=10).json()
    except Exception:
        print("La app no está en marcha. Ábrela con el acceso directo y vuelve a ejecutar.")
        sys.exit(2)
    t = tick()
    s = t["salud"]
    print("\n1) Conexión en tiempo real")
    check(s["ws"]["conectado"], "WebSocket de Kraken conectado", f'{s["ws"]["msg_s"]} msg/s · latencia {s["ws"]["latencia_ms"]} ms')
    check(s["ws"]["checksum_mal"] == 0 and s["ws"]["checksum_ok"] > 0, "Libros de órdenes íntegros (CRC32)", f'{s["ws"]["checksum_ok"]} ok · {s["ws"]["checksum_mal"]} fallos')
    check(s["rt_ms"] is not None and time.time() * 1000 - s["rt_t"] < 5000, "Análisis en vivo recalculándose", f'{s["rt_ms"]:.0f} ms por ciclo')
    check(s["last"] and time.time() * 1000 - s["last"] < 15 * 60000, "Motor v21 actualizado hace menos de 15 min")

    print("\n2) Precios contra consultas independientes")
    ap = requests.get("https://api.kraken.com/0/public/Ticker?pair=" + ",".join(
        ["XXBTZEUR", "XETHZEUR", "XXRPZEUR", "SOLEUR", "HYPEEUR", "SUIEUR", "XZECZEUR", "PAXGEUR"]), timeout=10).json()["result"]
    kmap = {"BTC": "XXBTZEUR", "ETH": "XETHZEUR", "XRP": "XXRPZEUR", "SOL": "SOLEUR", "HYPE": "HYPEEUR", "SUI": "SUIEUR", "ZEC": "XZECZEUR", "PAXG": "PAXGEUR"}
    for sym, c in t["coins"].items():
        kr = float(ap[kmap[sym]]["c"][0])
        try:
            cb = float(requests.get(f"https://api.coinbase.com/v2/prices/{sym}-EUR/spot", timeout=8).json()["data"]["amount"])
        except Exception:  # noqa: BLE001
            cb = None
        d1 = abs(c["price"] / kr - 1) * 100
        d2 = abs(c["price"] / cb - 1) * 100 if cb else None
        check(d1 <= .5 and (d2 is None or d2 <= 1.5), f"{sym:5} app {c['price']:.6g} €",
              f"Kraken REST {kr:.6g} ({d1:.2f} %)" + (f" · Coinbase {cb:.6g} ({d2:.2f} %)" if cb else " · Coinbase sin par"))

    print("\n3) Coherencia del análisis")
    for sym, o in st["data"].items():
        if o.get("fail"):
            check(False, f"{sym} sin análisis", o["fail"])
            continue
        z = t["coins"][sym]["z"]
        p = t["coins"][sym]["price"]
        ok_z = all(g["mid"] < p for g in z["sup"]) and all(g["mid"] > p for g in z["res"])
        tm = t["coins"][sym]["tm"]
        ok_t = tm and tm["bn"] == sum(1 for x in tm["B"] if x[1]) and tm["sn"] == sum(1 for x in tm["S"] if x[1])
        vv = t["coins"][sym]["vivo"]
        check(ok_z and ok_t and vv and vv["ts"] is not None, f"{sym:5} entradas bajo el precio, salidas encima, semáforo y lectura en vivo coherentes")

    print("\n4) Derivados contra Binance")
    for sym in ("BTC", "ETH", "SOL"):
        app = st["deriv"].get(sym, {}).get("funding")
        b = float(requests.get(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}USDT", timeout=8).json()["lastFundingRate"])
        check(app is not None and abs(app - b) < 5e-5, f"{sym} funding app {app} vs Binance {b}")

    print("\n5) Noticias y agenda")
    est = s["noticias"]
    activas = sum(1 for v in est.values() if v["ok"])
    check(activas >= 12, f"Fuentes de noticias activas: {activas}/{len(est)}")
    check(len(st["noticias"]) >= 20, f"Historias analizadas: {len(st['noticias'])}")
    check(s["cal_ok"], "Calendario macro disponible")
    sent = st.get("sent")
    check(sent and 0 <= sent["valor"] <= 100, f"Sentimiento compuesto: {sent and round(sent['valor'])} ({sent and sent['lectura']})")

    print(f"\n{'TODO CORRECTO' if not fallos else str(fallos) + ' comprobación(es) fallida(s)'}\n")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
