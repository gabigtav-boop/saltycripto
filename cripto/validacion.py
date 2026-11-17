"""Significación estadística de la validación técnica.

`motor.validate` (traducción exacta de la v21) calcula la correlación entre la puntuación técnica y el retorno posterior a
5 días, pero la da como un número sin margen de error. Con ~250 muestras por moneda, tomadas cada 2 días con horizonte de 5
(se solapan), y monedas que se mueven casi juntas, la muestra independiente es mucho menor que el recuento bruto, y un
valor como +0,12 puede ser ruido. Aquí se le pone intervalo con un remuestreo por bloques de tiempo: se barajan trozos
de 10 días con todas las monedas dentro, así se respetan el solapamiento y la correlación entre monedas.

El veredicto «ventaja modesta» solo se mantiene si el intervalo excluye el cero; si no, pasa a «posible, no demostrada».
`validate` no se toca: su paridad con el JavaScript original sigue siendo exacta."""
import numpy as np

from .motor import score_from

BLOQUE = 5          # muestras por bloque (cada muestra está a 2 días de la anterior: 10 días)
REMUESTRAS = 400
NIVEL = .90
MIN_BLOQUES = 12


def intervalo_ic(data, bloque=BLOQUE, remuestras=REMUESTRAS, nivel=NIVEL, semilla=7):
    """Intervalo de la correlación puntuación/retorno por remuestreo de bloques temporales. Las muestras de cada moneda se
    alinean por su distancia al final de la serie (todas terminan en la última vela diaria cerrada)."""
    xs, ys, bq = [], [], []
    for o in data.values():
        bt = o.get("bt") if o else None
        if not bt or not bt.get("SM"):
            continue
        sm = bt["SM"]
        for j, p in enumerate(sm):
            sc = score_from(p["v"], p["rg"])["score"]
            if sc is not None:
                xs.append(sc)
                ys.append(p["f"])
                bq.append((len(sm) - 1 - j) // bloque)
    if len(xs) < 30:
        return None
    x, y, b = np.array(xs), np.array(ys), np.array(bq)
    grupos = [np.flatnonzero(b == i) for i in np.unique(b)]
    if len(grupos) < MIN_BLOQUES:
        return None
    rng = np.random.default_rng(semilla)
    ics = []
    for _ in range(remuestras):
        ix = np.concatenate([grupos[k] for k in rng.integers(0, len(grupos), len(grupos))])
        xv, yv = x[ix], y[ix]
        if xv.std() > 0 and yv.std() > 0:
            ics.append(float(np.corrcoef(xv, yv)[0, 1]))
    if len(ics) < remuestras // 2:
        return None
    lo, hi = np.quantile(ics, [(1 - nivel) / 2, 1 - (1 - nivel) / 2])
    return {"lo": float(lo), "hi": float(hi), "n": int(len(x)), "bloques": len(grupos), "nivel": nivel}


def con_intervalo(val, data):
    """Añade el intervalo a la validación y no deja que el veredicto afirme una ventaja que el intervalo no respalda."""
    if not val:
        return val
    ci = intervalo_ic(data)
    val["ic_int"] = ci
    if ci and val["vd"][0] == "AL" and ci["lo"] <= 0:
        val["vd"] = ["NE", "Ventaja posible, no demostrada"]
    return val
