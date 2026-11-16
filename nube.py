"""Saltycripto · versión nube: ejecuta el motor UNA vez y publica una "foto" del análisis como web estática.

Lo usa GitHub Actions con frecuencia (ver el cron) (.github/workflows/publicar.yml). También se puede probar en local:
    .venv/bin/python nube.py            → genera publico/
    .venv/bin/python -m http.server -d publico 8600   → y se abre http://localhost:8600

Qué genera en publico/:
  index.html, favicon.svg, vendor/…   la misma interfaz, en modo estático
  datos/estado.json                   el análisis completo de las 8 monedas (como /api/state)
  datos/moneda/<SÍMBOLO>.json         velas de las 5 temporalidades y el rango probable completo (para el Estudio)
Los precios, el gráfico y el libro se mueven en vivo en el navegador, que se conecta directamente a Kraken."""
import json
import pathlib
import shutil
import sys
import time

from cripto.servicio import COINS, Servicio, ligero

RAIZ = pathlib.Path(__file__).resolve().parent
WEB = RAIZ / "web"
SALIDA = RAIZ / "publico"
TFS = ["1m", "1h", "4h", "1d", "1w"]


def escribir(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ligero(obj), ensure_ascii=False, separators=(",", ":")))
    return path.stat().st_size


def main():
    t0 = time.time()
    sv = Servicio()
    sv.con_ws = False                      # sin conexión en vivo: todo por REST
    print("· análisis completo (Kraken, Coinbase, CoinGecko, histórico, rango)…", flush=True)
    sv.main(False)
    if not sv.data:
        print("✗ sin datos de Kraken; no se publica nada", flush=True)
        sys.exit(1)
    print("· noticias, agenda, derivados y sentimiento…", flush=True)
    sv.actualizar_fundamental()
    sv.actualizar_derivados()
    print("· lectura en vivo, niveles y rango probable…", flush=True)
    sv.tiempo_real()
    sv.calc_sentimiento()
    sv.bar = "análisis en la nube"

    # web
    if SALIDA.exists():
        shutil.rmtree(SALIDA)
    (SALIDA / "vendor").mkdir(parents=True)
    shutil.copy(WEB / "vendor" / "lightweight-charts.js", SALIDA / "vendor" / "lightweight-charts.js")
    shutil.copy(WEB / "favicon.svg", SALIDA / "favicon.svg")
    html = (WEB / "index.html").read_text()
    html = html.replace("<script>\n\"use strict\";", "<script>window.ESTATICO=true;</script>\n<script>\n\"use strict\";", 1)
    (SALIDA / "index.html").write_text(html)
    (SALIDA / ".nojekyll").write_text("")

    # estado general
    estado = json.loads(sv.snapshot())
    salud = sv.salud()
    estado["nube"] = {"generado": int(time.time() * 1000), "duracion_s": round(time.time() - t0, 1),
                      "salud": salud}
    total = escribir(SALIDA / "datos" / "estado.json", estado)

    # por moneda: velas de cada temporalidad (para el gráfico) y rango probable completo (abanico, casos y niveles)
    for s in COINS:
        o = sv.data.get(s) or {}
        if not o.get("price"):
            continue
        velas = {}
        for tf in TFS:
            try:
                velas[tf] = json.loads(sv.velas(s, tf))
            except Exception:  # noqa: BLE001
                pass
        # la web recalcula la probabilidad de llegar a cada nivel con el precio en vivo: necesita las curvas de supervivencia
        proy = {}
        for tf, x in (o.get("proy") or {}).items():
            c = (o.get("calib") or {}).get(tf) or {}
            proy[tf] = dict(x, **({"F_up": [round(float(v), 4) for v in c["F_up"]], "F_dn": [round(float(v), 4) for v in c["F_dn"]]}
                                  if "F_up" in c and "F_dn" in c else {}))
        total += escribir(SALIDA / "datos" / "moneda" / f"{s}.json", {"proy": proy, "velas": velas})

    ok = sum(1 for o in sv.data.values() if o and o.get("price"))
    print(f"✓ publicado en {SALIDA.relative_to(RAIZ)}/ · {ok}/{len(COINS)} monedas · {total / 1e6:.1f} MB · {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
