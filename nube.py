"""Saltycripto · versión nube: ejecuta el motor UNA vez y publica una "foto" del análisis como web estática.

Lo usa GitHub Actions con frecuencia (ver el cron) (.github/workflows/publicar.yml). También se puede probar en local:
    .venv/bin/python nube.py            → genera publico/
    .venv/bin/python -m http.server -d publico 8600   → y se abre http://localhost:8600

Qué genera en publico/:
  index.html, favicon.svg, vendor/…   la misma interfaz, en modo estático
  datos/estado.json                   el análisis completo de las 8 monedas (como /api/state)
  datos/moneda/<SÍMBOLO>.json         velas de las 5 temporalidades y el rango probable completo (para el Estudio)
  datos/evidencias/…                  proyecciones 1 h · 24 h guardadas y su verificación (Datos › Diagnóstico)
  catalogo.js                         el catálogo de monedas para la página (cripto/catalogo.py)
La base de evidencias (datos/decisiones.sqlite3) se conserva entre ejecuciones con actions/cache: cada ejecución emite la
proyección de la hora si el precio es vigente, guarda las velas de 1 min de Kraken (REST, últimas 12 h) y verifica las
vencidas. Las horas sin ejecución cuentan como servicio apagado y no se reconstruyen.
Los precios, el gráfico y el libro se mueven en vivo en el navegador, que se conecta directamente a Kraken."""
import json
import pathlib
import shutil
import sys
import time

from cripto import catalogo
from cripto.servicio import COINS, Servicio, ligero

RAIZ = pathlib.Path(__file__).resolve().parent
WEB = RAIZ / "web"
SALIDA = RAIZ / "publico"
TFS = ["1m", "1h", "4h", "1d", "1w"]
EV_LISTA = 48          # emisiones por moneda en la lista del diagnóstico (y con su detalle publicado)


def escribir(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ligero(obj), ensure_ascii=False, separators=(",", ":")))
    return path.stat().st_size


def evidencias(sv):
    """Velas de 1 min, horas sin emisión, verificación de las vencidas e historial. Devuelve la hora de la ejecución."""
    if not sv.evid:
        print(f"· evidencias no disponibles: {sv.ev_error}", flush=True)
        return None
    ahora = int(time.time() * 1000)
    print("· evidencias: velas de 1 min de Kraken, verificación e historial…", flush=True)
    sv.ciclo_evidencias(ahora)
    # el proceso termina aquí: la hora en curso sin emisión se anota ya (si otra ejecución de esta hora emite, se borra)
    for (s, h), motivo in list(sv._sin_emitir.items()):
        try:
            sv.evid.omision(s, h, motivo, ahora)
        except Exception as e:  # noqa: BLE001
            print(f"omisión {s}: {e!r}", flush=True)
    sv._sin_emitir.clear()
    cal = sv.calidad(ahora)                # con el historial recién calculado
    cal.pop("t", None)
    sv._calidad = cal
    return ahora


def publicar_evidencias(sv, ahora):
    """Lo que en el Mac sirven /api/historial, /api/evidencias, /api/minutos y /api/evidencia, como archivos estáticos."""
    if ahora is None or not sv.evid:
        return 0
    base = SALIDA / "datos" / "evidencias"
    total = escribir(base / "historial.json", sv.hist_ev or sv.evid.historial(ahora, tuple(sv.activos_ev())))
    n = 0
    for s in sv.activos_ev():
        try:
            r = sv.evid.resumen(s, ahora, EV_LISTA)
            total += escribir(base / f"{s}.json", r)
            total += escribir(base / f"{s}_min.json", {"s": s, "minutos": sv.evid.minutos(s, 180, ahora)})
            for x in r["lista"]:
                d = sv.evid.detalle(s, x["hora"])
                if d:
                    total += escribir(base / s / f"{x['hora']}.json", d)
                    n += 1
        except Exception as e:  # noqa: BLE001
            print(f"evidencias {s}: {e!r}", flush=True)
    print(f"· evidencias publicadas: {n} emisiones con detalle", flush=True)
    return total


def main():
    t0 = time.time()
    sv = Servicio()
    sv.con_ws = False                      # sin conexión en vivo: todo por REST
    sv.registro = None                     # el registro de decisiones spot (SQLite) es del Mac: aquí cada ejecución es una foto suelta
    sv.avisos = None                       # los avisos trazables necesitan el servidor encendido: solo en el Mac
    print("· análisis completo (Kraken, Coinbase, CoinGecko, histórico, rango)…", flush=True)
    sv.main(False)
    if not sv.data:
        print("✗ sin datos de Kraken; no se publica nada", flush=True)
        sys.exit(1)
    print("· noticias, agenda, derivados y sentimiento…", flush=True)
    sv.actualizar_fundamental()
    sv.actualizar_derivados()
    print("· lectura en vivo, niveles y rango probable…", flush=True)
    sv.live_rest()                         # cotización de ahora (vigente solo si está dentro de la horquilla del libro)
    sv.tiempo_real()                       # también emite la proyección 1 h · 24 h de la hora si el precio es vigente
    sv.calc_sentimiento()
    evid = evidencias(sv)
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
    (SALIDA / "catalogo.js").write_text("window.CATALOGO=" + json.dumps(catalogo.web(), ensure_ascii=False, separators=(",", ":")) + ";")
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

    total += publicar_evidencias(sv, evid)

    ok = sum(1 for o in sv.data.values() if o and o.get("price"))
    print(f"✓ publicado en {SALIDA.relative_to(RAIZ)}/ · {ok}/{len(COINS)} monedas · {total / 1e6:.1f} MB · {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
