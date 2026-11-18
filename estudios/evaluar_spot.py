"""Evaluación prospectiva por activo de la decisión spot «Esperar una zona». Lee la base sin modificarla.

Para cada decisión «Esperar» con zona (una por activo y hora, para que una moneda que cambia mucho de señal no pese más) se
comparan tres alternativas con la MISMA salida (el precio registrado al cumplirse 24 h) y el MISMO coste por lado:
  · espera   compra al primer precio registrado dentro de la zona en esas 24 h; si no llega, se queda en efectivo (0 %)
  · inmediata compra al precio de la decisión
  · repartida mitad al precio de la decisión y mitad al primer precio registrado a partir de 12 h
Los precios son una cotización por minuto (último negociado en Kraken). Una visita breve a la zona entre dos muestras puede
pasar inadvertida. Se excluyen, y se cuentan por motivo, los casos que no pueden evaluarse."""
import argparse
import collections
import json
import pathlib
import sqlite3
import statistics
import time

MIN = 60_000
DIA = 1440 * MIN
HUECO_MAX = 3 * MIN
RUTA = pathlib.Path(__file__).resolve().parents[1] / "datos" / "decisiones.sqlite3"
MOTIVOS = {"repetida": "repetida en la misma hora", "otra": "otra decisión (no «Esperar»)", "sin_zona": "«Esperar» sin zona",
           "pendiente": "pendientes de 24 h", "hueco": "sin seguimiento suficiente"}


def evaluar(db, ahora, coste):
    """Devuelve (motivos de exclusión y pendientes, {activo: [(zona tocada, espera, inmediata, repartida), …]})."""
    grupos = collections.defaultdict(list)
    estados = collections.Counter()
    vistos = set()
    for sym, t, raw in db.execute("SELECT simbolo,t,detalle FROM decisiones ORDER BY t"):
        d = json.loads(raw)
        k = (sym, t // (60 * MIN))
        if k in vistos:
            estados[MOTIVOS["repetida"]] += 1
            continue
        vistos.add(k)
        if d.get("accion") != "E":
            estados[MOTIVOS["otra"]] += 1
            continue
        if not d.get("zona"):
            estados[MOTIVOS["sin_zona"]] += 1
            continue
        if ahora < t + DIA + 2 * MIN:
            estados[MOTIVOS["pendiente"]] += 1
            continue
        filas = db.execute("SELECT t,precio FROM precios WHERE simbolo=? AND minuto BETWEEN ? AND ? ORDER BY minuto",
                           (sym, t // MIN, (t + DIA + 2 * MIN) // MIN))
        puntos = [(ts, p) for ts, p in filas if t < ts <= t + DIA + 2 * MIN]
        fin = next(((ts, p) for ts, p in puntos if ts >= t + DIA), None)
        tiempos = [ts for ts, _ in puntos]
        if not fin or any(b - a > HUECO_MAX for a, b in zip([t] + tiempos, tiempos)):
            estados[MOTIVOS["hueco"]] += 1
            continue
        lo, hi = d["zona"]
        entrada = next((p for ts, p in puntos if ts <= t + DIA and lo <= p <= hi), None)
        mitad = next(p for ts, p in puntos if ts >= t + DIA // 2)

        def neto(precio):
            return fin[1] * (1 - coste) / (precio * (1 + coste)) - 1        # misma salida y mismo coste por lado
        inmediato = neto(d["precio"])
        grupos[sym].append((entrada is not None, neto(entrada) if entrada else 0.,
                            inmediato, (inmediato + neto(mitad)) / 2))
    return estados, grupos


def informe(db, ahora, coste):
    """Resultado en un diccionario (para la pestaña Datos y para la consola)."""
    estados, grupos = evaluar(db, ahora, coste)
    total = sum(estados.values()) + sum(len(g) for g in grupos.values())
    activos = {}
    for sym, filas in grupos.items():
        tocadas, espera, inmediata, partida = zip(*filas)
        activos[sym] = {"n": len(filas), "zona_tocada": sum(tocadas), "espera": statistics.mean(espera),
                        "inmediata": statistics.mean(inmediata), "repartida": statistics.mean(partida)}
    return {"coste": coste, "total": total, "evaluadas": sum(a["n"] for a in activos.values()),
            "excluidas": dict(estados), "activos": activos,
            "metodo": "Misma salida (precio registrado a 24 h) y mismo coste por lado para las tres alternativas; "
                      "observaciones horarias solapadas; una cotización por minuto (último negociado en Kraken)."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coste", type=float, default=.001, help="Coste simulado por lado, p. ej. 0.001")
    args = ap.parse_args()
    if not 0 <= args.coste < 1:
        ap.error("El coste debe estar entre 0 y 1")
    if not RUTA.exists():
        print("Aún no hay decisiones. Abre SaltyCripto para empezar a registrarlas.")
        return
    with sqlite3.connect(RUTA.as_uri() + "?mode=ro", uri=True) as db:
        r = informe(db, time.time() * 1000, args.coste)
    print("Esperar una zona durante 24 h · observaciones horarias solapadas")
    print(f"Coste simulado por lado: {args.coste:.2%}. Misma salida para las tres alternativas.")
    print(f"Decisiones registradas: {r['total']} · evaluadas: {r['evaluadas']}")
    for k, n in r["excluidas"].items():
        print(f"  excluidas · {k}: {n}")
    for sym, a in r["activos"].items():
        print(f"{sym}: {a['n']} observaciones · zona observada {a['zona_tocada']} · "
              f"espera {a['espera']:+.2%} · inmediata {a['inmediata']:+.2%} · "
              f"mitad ahora y mitad a 12 h {a['repartida']:+.2%}")
    if not r["activos"]:
        print("Todavía no hay esperas con seguimiento suficiente.")
    print("Rendimientos medios simulados por observación. Se usa una cotización por minuto. "
          "Una visita entre muestras puede pasar inadvertida. Sin visita observada, la estrategia de espera conserva efectivo.")


if __name__ == "__main__":
    main()
