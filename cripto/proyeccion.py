"""Proyección por analogías: la flecha del gráfico.

Cómo funciona (es el método de "pronóstico por análogos", habitual en meteorología y en mesas cuantitativas,
adaptado a mercados):

1) Rasgos. En cada vela cerrada del histórico se describe el estado del mercado con 14 magnitudes sin escala
   (lectura técnica v21, RSI, distancia a las medias en ATR, impulso normalizado por volatilidad, Bollinger,
   régimen de volatilidad, ADX, volumen relativo, posición en el rango, hora del día…).
2) Resultado real. Para cada una de esas velas se mide cuánto se movió el precio en las H velas siguientes,
   dividido por la volatilidad del momento (así un día tranquilo y uno agitado se comparan en igualdad).
3) Analogías. Para el estado actual se buscan las k situaciones pasadas más parecidas (vecinos más cercanos,
   con los rasgos ponderados por su capacidad predictiva en el propio histórico y sin contar dos veces
   momentos solapados). La distribución de lo que pasó después, reescalada a la volatilidad actual, da el
   recorrido central, el abanico de percentiles (10-25-50-75-90) y la probabilidad de subida.
4) Validación fuera de muestra. Antes de fiarse, el método se pone a prueba sobre el último tramo del
   histórico usando solo información anterior a cada punto: acierto direccional, cobertura real del cono y
   "destreza" frente a no saber nada (pérdida pinball). Si no hay destreza, la flecha se queda en lateral y
   lo dice.
5) Inclinación acotada (±0,35 σ) por los factores no técnicos de la lectura combinada (noticias, derivados,
   flujo de órdenes, sentimiento) y desplazamiento por lo que el precio ya ha recorrido en la vela en curso.

Todo se calcula con velas cerradas: ninguna muestra usa información posterior a su propio instante."""
import math

import numpy as np

from .motor import analyze

HOR = {"1m": 60, "1h": 24, "4h": 12, "1d": 7, "1w": 4}         # velas que se proyectan
HORAS = {"1m": 1 / 60, "1h": 1, "4h": 4, "1d": 24, "1w": 168}   # horas por vela
PASO = {"1m": 6, "1h": 3, "4h": 2, "1d": 1, "1w": 1}            # separación entre muestras del histórico
CON_HORA = {"1m", "1h"}
WARM = 100
MIN_MUESTRAS = 60
QS = (.1, .25, .5, .75, .9)
RASGOS = ["lectura técnica v21", "RSI 14", "distancia a EMA20", "distancia a EMA50", "distancia a EMA200",
          "impulso del horizonte", "impulso corto", "Bollinger %B", "régimen de volatilidad", "ADX",
          "volumen relativo", "posición en el rango", "hora del día (sen)", "hora del día (cos)"]


# ---------- series ----------
def _ema(x, p):
    k = 2 / (p + 1)
    o = np.empty_like(x)
    e = x[0]
    for i in range(len(x)):
        e = x[i] * k + e * (1 - k)
        o[i] = e
    return o


def _wilder(x, p, inicio):
    o = np.full(len(x), np.nan)
    if len(x) <= inicio + p:
        return o
    a = x[inicio:inicio + p].mean()
    o[inicio + p - 1] = a
    for i in range(inicio + p, len(x)):
        a = (a * (p - 1) + x[i]) / p
        o[i] = a
    return o


def _rsi(c, p=14):
    d = np.diff(c, prepend=c[0])
    g = _wilder(np.maximum(d, 0), p, 1)
    l = _wilder(np.maximum(-d, 0), p, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + g / l)
    r = np.where(l == 0, 100, r)
    return np.nan_to_num(r, nan=50.0)


def _adx(h, l, c, p=14):
    n = len(c)
    up = np.diff(h, prepend=h[0])
    dn = -np.diff(l, prepend=l[0])
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    P, M, T = _wilder(pdm, p, 1), _wilder(mdm, p, 1), _wilder(tr, p, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = 100 * np.abs(P - M) / np.where(P + M == 0, 1, P + M)
    dx = np.nan_to_num(dx, nan=0.0)
    a = _wilder(dx, p, 2 * p)
    return np.nan_to_num(a, nan=20.0), np.nan_to_num(T, nan=np.nanmean(T[np.isfinite(T)]) if np.isfinite(T).any() else 1.0)


def _roll(x, w, fn):
    """fn (max/min/mean) sobre ventana w, alineada al final; los primeros w-1 valores se rellenan."""
    if len(x) < w:
        return np.full(len(x), fn(x) if len(x) else 0.0)
    v = np.lib.stride_tricks.sliding_window_view(x, w)
    out = fn(v, axis=1)
    return np.concatenate([np.full(w - 1, out[0]), out])


def rasgos(rows, tf):
    """Rasgos sin escala en cada vela, volatilidad del momento y precios (todo con velas cerradas)."""
    H = HOR[tf]
    n = len(rows)
    t = np.array([r["t"] for r in rows], dtype=float)
    o = np.array([r["o"] for r in rows], dtype=float)
    h = np.array([r["h"] for r in rows], dtype=float)
    l = np.array([r["l"] for r in rows], dtype=float)
    c = np.array([r["c"] for r in rows], dtype=float)
    v = np.array([r["v"] for r in rows], dtype=float)
    lc = np.log(np.maximum(c, 1e-12))
    lr = np.diff(lc, prepend=lc[0])
    sig_s = np.sqrt(np.maximum(_ema(lr * lr, 20), 1e-14))
    sig_l = np.sqrt(np.maximum(_ema(lr * lr, 100), 1e-14))
    sigma = np.clip(sig_s, .5 * sig_l, 2 * sig_l)
    e20, e50 = _ema(c, 20), _ema(c, 50)
    e200 = _ema(c, 200) if n >= 220 else None
    adx, atr = _adx(h, l, c)
    atr = np.maximum(atr, c * 1e-5)
    rsi = _rsi(c)
    m20 = _roll(c, 20, np.mean)
    s20 = np.maximum(np.sqrt(np.maximum(_roll(c * c, 20, np.mean) - m20 * m20, 0)), c * 1e-6)
    bbp = (c - (m20 - 2 * s20)) / (4 * s20)
    vm = _roll(v, 20, np.mean)
    relvol = np.log(np.maximum(v, 1e-9) / np.maximum(vm, 1e-9))
    hi50, lo50 = _roll(h, 50, np.max), _roll(l, 50, np.min)
    rng = (c - lo50) / np.maximum(hi50 - lo50, c * 1e-6)
    q = max(2, H // 4)
    momH = (lc - np.concatenate([np.full(H, lc[0]), lc[:-H]])) / (sigma * math.sqrt(H))
    momq = (lc - np.concatenate([np.full(q, lc[0]), lc[:-q]])) / (sigma * math.sqrt(q))
    F = np.zeros((n, len(RASGOS)))
    F[:, 1] = (rsi - 50) / 20
    F[:, 2] = np.clip((c - e20) / atr, -6, 6)
    F[:, 3] = np.clip((c - e50) / atr, -8, 8)
    F[:, 4] = np.clip((c - e200) / atr, -12, 12) / 2 if e200 is not None else 0
    F[:, 5] = np.clip(momH, -4, 4)
    F[:, 6] = np.clip(momq, -4, 4)
    F[:, 7] = np.clip((bbp - .5) * 2, -2, 2)
    F[:, 8] = np.clip(np.log(sig_s / sig_l), -1.5, 1.5) * 2
    F[:, 9] = np.clip(adx / 25 - 1, -1, 2)
    F[:, 10] = np.clip(relvol, -2, 2)
    F[:, 11] = (rng - .5) * 2
    if tf in CON_HORA:
        hora = ((t / 3.6e6) % 24) / 24 * 2 * math.pi
        F[:, 12], F[:, 13] = np.sin(hora), np.cos(hora)
    return {"F": F, "sigma": sigma, "lc": lc, "t": t, "c": c, "H": H, "atr": atr}


def _score21(rows, idx, cache=None):
    """Lectura técnica v21 (0-100) en las velas pedidas, con solo velas anteriores.
    `cache` (marca de tiempo → lectura) evita recalcular velas cerradas, que nunca cambian. Solo se guardan
    las que tienen la ventana completa de 260 velas, así el valor no depende de dónde empiece la serie
    (y la lectura no cambia si toda la serie se reescala, como al empalmar histórico y Kraken)."""
    out = {}
    for i in idx:
        t = rows[i]["t"]
        if cache is not None and i >= 259 and t in cache:
            out[i] = cache[t]
            continue
        a = analyze(rows[max(0, i - 259):i + 1], False)
        out[i] = a["score"] if (a and a["score"] is not None) else 50.0
        if cache is not None and i >= 259:
            cache[t] = out[i]
    return out


# ---------- utilidades estadísticas ----------
def wquant(x, w, qs=QS):
    o = np.argsort(x)
    x, w = x[o], w[o]
    cw = np.cumsum(w)
    cw = (cw - .5 * w) / cw[-1]
    return np.interp(qs, cw, x)


def _pinball(q, y, qs=QS):
    """Pérdida pinball media de un conjunto de cuantiles frente al valor real."""
    s = 0.0
    for qq, a in zip(q, qs):
        d = y - qq
        s += max(a * d, (a - 1) * d)
    return s / len(qs)


def _vecinos(X, Xq, w, idx, sep, k):
    """k vecinos más parecidos, sin dos momentos a menos de `sep` velas (no se cuenta dos veces lo mismo)."""
    d2 = ((X - Xq) ** 2 * w).sum(1)
    m = len(d2)
    pre = min(m, 14 * k)
    cand = np.argpartition(d2, pre - 1)[:pre] if pre < m else np.arange(m)
    cand = cand[np.argsort(d2[cand])]
    bloq = np.zeros(int(idx.max()) + sep + 1, dtype=bool)
    sel = []
    for j in cand:
        tj = int(idx[j])
        if bloq[tj]:
            continue
        sel.append(j)
        bloq[max(0, tj - sep + 1):tj + sep] = True
        if len(sel) >= k:
            break
    sel = np.array(sel)
    d = np.sqrt(d2[sel])
    dk = d[-1] if d[-1] > 0 else 1.0
    u = np.minimum(d / dk, 1.0)
    pesos = (1 - u ** 3) ** 3 + .05
    return sel, pesos


def _pesos_rasgos(X, y):
    """Peso de cada rasgo = su correlación (en valor absoluto) con el resultado, con un suelo."""
    Xc = X - X.mean(0)
    yc = y - y.mean()
    sx = np.sqrt((Xc ** 2).sum(0)) + 1e-12
    sy = math.sqrt((yc ** 2).sum()) + 1e-12
    ic = np.abs((Xc * yc[:, None]).sum(0) / (sx * sy))
    ic[X.std(0) < 1e-9] = 0
    w = ic + .02
    return w / w.mean(), ic


def _estandar(X):
    mu = X.mean(0)
    sd = X.std(0)
    sd[sd < 1e-9] = 1
    return mu, sd


def _k(n):
    return int(min(60, max(15, round(1.6 * math.sqrt(n)))))


def _pron(y, P, w):
    """Cuantiles del resultado y del recorrido, y probabilidad de subida, de un conjunto ponderado."""
    q = wquant(y, w)
    qp = np.array([wquant(P[:, i], w) for i in range(P.shape[1])])     # H × 5
    p_up = float(w[y > 0].sum() / w.sum())
    return q, qp, p_up


def _mezcla(a, b, lam):
    return lam * a + (1 - lam) * b


def _factores(Q, Y):
    """Ajuste conformal: cuánto ensanchar las bandas 50 % y 80 % para que cubran lo que prometen."""
    med = Q[:, 2]
    h50 = np.maximum((Q[:, 3] - Q[:, 1]) / 2, 1e-6)
    h80 = np.maximum((Q[:, 4] - Q[:, 0]) / 2, 1e-6)
    r = np.abs(Y - med)
    f50 = float(np.quantile(r / h50, .5))
    f80 = float(np.quantile(r / h80, .8))
    return min(3.0, max(.7, f50)), min(3.0, max(.7, f80))


def _ensanchar(Q, f50, f80):
    """Aplica los factores a cuantiles (filas: casos u horizontes; columnas: 10-25-50-75-90)."""
    med = Q[:, 2:3]
    out = Q.copy()
    out[:, 1:2] = med - (med - Q[:, 1:2]) * f50
    out[:, 3:4] = med + (Q[:, 3:4] - med) * f50
    out[:, 0:1] = med - (med - Q[:, 0:1]) * f80
    out[:, 4:5] = med + (Q[:, 4:5] - med) * f80
    return out


# ---------- calibración (histórico) ----------
def calibrar(rows, tf, cache=None):
    """Prepara el histórico de una temporalidad: rasgos, resultados, validación fuera de muestra
    y el pronóstico para el último estado conocido (última vela cerrada)."""
    H = HOR[tf]
    paso = PASO[tf]
    n = len(rows)
    if n < WARM + H + MIN_MUESTRAS * paso // 2:
        return None
    R = rasgos(rows, tf)
    F, sigma, lc = R["F"], R["sigma"], R["lc"]
    idx = np.arange(WARM, n - H, paso)
    if len(idx) < MIN_MUESTRAS:
        return None
    s21 = _score21(rows, list(idx) + [n - 1], cache)
    for i, s in s21.items():
        F[i, 0] = (s - 50) / 25
    den = sigma[idx] * math.sqrt(H)
    y = (lc[idx + H] - lc[idx]) / den
    P = np.stack([(lc[idx + i] - lc[idx]) / den for i in range(1, H + 1)], axis=1)
    k = _k(len(idx))
    sep = max(1, H // 2)
    m = len(idx)
    # --- validación fuera de muestra: último 35 % del histórico, cada punto solo con lo anterior a él.
    # Tramo A: cuánto ensanchar el cono para que cubra lo que promete (ajuste conformal).
    # Tramo B: con el cono ya ajustado, acierto direccional, cobertura y destreza frente a "no saber nada"
    # (la distribución de siempre, sin deriva).
    corte = int(m * .65)
    w_r, ic = _pesos_rasgos(F[idx[:corte]], y[:corte])
    mu, sd = _estandar(F[idx[:corte]])
    Z = (F[idx] - mu) / sd
    pruebas = range(corte, m, max(1, round(H / 4 / paso)) if paso < H else 1)
    Q, PU, Y, QB = [], [], [], []
    for i in pruebas:
        lim = int(np.searchsorted(idx, idx[i] - H, side="right"))   # muestras cuyo resultado ya se conocía
        if lim < MIN_MUESTRAS:
            continue
        sel, pw = _vecinos(Z[:lim], Z[i], w_r, idx[:lim], sep, k)
        q, _, p_up = _pron(y[sel], P[sel][:, -1:], pw)
        Q.append(q)
        PU.append(p_up)
        Y.append(y[i])
        qb = np.quantile(y[:lim], QS)
        QB.append(qb - qb[2])
    val = None
    fk, fb = (1.3, 1.3), (1.0, 1.0)
    destreza = None
    if len(Y) >= 24:
        Q, PU, Y, QB = np.array(Q), np.array(PU), np.array(Y), np.array(QB)
        a = len(Y) // 2
        fkA, fbA = _factores(Q[:a], Y[:a]), _factores(QB[:a], Y[:a])
        Qw, QBw = _ensanchar(Q[a:], *fkA), _ensanchar(QB[a:], *fbA)
        Yb, PUb = Y[a:], PU[a:]
        sube = (PUb >= .55) & (Qw[:, 2] > 0)
        baja = (PUb <= .45) & (Qw[:, 2] < 0)
        llam = int(sube.sum() + baja.sum())
        hit = int((sube & (Yb > 0)).sum() + (baja & (Yb < 0)).sum())
        pin_m = float(np.mean([_pinball(qq, yy) for qq, yy in zip(Qw, Yb)]))
        pin_b = float(np.mean([_pinball(qq, yy) for qq, yy in zip(QBw, Yb)]))
        destreza = 1 - pin_m / pin_b if pin_b > 0 else 0.0
        lam_v = _lam(destreza, float(len(Yb)))
        Fin = _mezcla(Qw, QBw, lam_v)
        val = {"n": int(len(Yb)), "llamadas": llam, "acierto": hit / llam if llam else None,
               "cob50": float(((Fin[:, 1] <= Yb) & (Yb <= Fin[:, 3])).mean()),
               "cob80": float(((Fin[:, 0] <= Yb) & (Yb <= Fin[:, 4])).mean()),
               "destreza": destreza, "base_sube": float((Yb > 0).mean()),
               "ic": float(np.corrcoef(Q[a:, 2], Yb)[0, 1]) if np.std(Q[a:, 2]) > 1e-9 else 0.0}
        fk, fb = _factores(Q, Y), _factores(QB, Y)
        val["f50"], val["f80"] = fk
    # --- pronóstico para el estado actual (última vela cerrada), con todo el histórico ---
    w_r, ic = _pesos_rasgos(F[idx], y)
    mu, sd = _estandar(F[idx])
    Z = (F[idx] - mu) / sd
    zq = (F[n - 1] - mu) / sd
    sel, pw = _vecinos(Z, zq, w_r, idx, sep, k)
    q, qp, p_up = _pron(y[sel], P[sel], pw)
    q = _ensanchar(q[None, :], *fk)[0]
    qp = _ensanchar(qp, *fk)
    n_eff = float(pw.sum() ** 2 / (pw ** 2).sum())
    # la distribución "de siempre": sin deriva (mediana cero) y con su propio ajuste de anchura
    qb = np.quantile(y, QS)
    qb = _ensanchar((qb - qb[2])[None, :], *fb)[0]
    qpb = np.quantile(P, QS, axis=0).T
    qpb = _ensanchar(qpb - qpb[:, 2:3], *fb)
    # cuánto se escuchan las analogías: solo en la medida en que demostraron destreza fuera de muestra
    # la dirección solo se escucha si, además de mejorar la anchura del cono, acertó al menos la mitad de las veces
    # que la marcó fuera de muestra: con un acierto por debajo del 50 % las analogías van peor que lanzar una moneda
    dir_ok = direccion_valida(val)
    lam = _lam(destreza, n_eff) if (destreza is not None and dir_ok) else 0.0
    fiable = destreza is not None and destreza > 0 and dir_ok
    orden = np.argsort(-ic)
    top = [{"rasgo": RASGOS[j], "ic": float(ic[j])} for j in orden[:4] if ic[j] > 0]
    o_sel = sel[np.argsort(-pw)][:6]
    lst = list(sel)
    analogos = [{"t": int(R["t"][idx[j]]), "ret": [float(x) for x in P[j]], "fin": float(y[j]), "peso": float(pw[lst.index(j)])} for j in o_sel]
    return {"tf": tf, "H": H, "horas": H * HORAS[tf], "n": int(n), "muestras": int(m), "k": int(k), "n_eff": n_eff, "lam": float(lam),
            "t_ultima": int(R["t"][n - 1]), "c_ultima": float(R["c"][n - 1]), "sigma": float(sigma[n - 1]),
            "q": _mezcla(q, qb, lam), "qp": _mezcla(qp, qpb, lam), "p_up": _mezcla(p_up, .5, lam),
            "q_ana": q, "p_up_ana": float(p_up), "val": val, "fiable": fiable, "rasgos": top, "analogos": analogos,
            "score21": s21[n - 1], "estado_z": [float(x) for x in zq[:12]]}


def direccion_valida(val, minimas=10):
    """¿Respalda la validación fuera de muestra la dirección? Sin validación o con pocas llamadas no se descarta
    (la destreza ya decide); con suficientes llamadas, el acierto debe ser al menos del 50 %."""
    if not val or val.get("llamadas", 0) < minimas or val.get("acierto") is None:
        return True
    return val["acierto"] >= .5


def _lam(destreza, n_eff):
    """Peso de las analogías frente a la distribución base: 0 sin destreza, 1 con destreza ≥ 5 %."""
    if destreza is None or destreza <= 0:
        return 0.0
    return min(1.0, destreza / .05) * n_eff / (n_eff + 12)


# ---------- proyección en vivo ----------
def proyectar(price, tf, calib, prev=None, z=None):
    """Convierte el pronóstico calibrado en precios desde el precio actual: centro, bandas, recorrido,
    probabilidad y nivel que puede frenarlo. Es instantáneo (se llama cada segundo)."""
    if not calib or not price or price <= 0:
        return None
    H = calib["H"]
    esc = calib["sigma"] * math.sqrt(H)             # desviación típica del horizonte, en log-retorno
    # inclinación por factores no técnicos (noticias, derivados, flujo, sentimiento), acotada a ±0,35 σ
    nt = 0.0
    if prev and prev.get("factores"):
        nt = sum(f["aporte"] for f in prev["factores"] if not f["nombre"].startswith("Técnico") and f["nombre"] != "Semáforo de momento") / 100
    incl = max(-.35, min(.35, nt * 1.2))
    # el pronóstico se ancla al precio actual (sin deriva no hay razón para "volver" al cierre anterior);
    # lo recorrido desde la última vela cerrada se informa, no se descuenta
    d = (math.log(price) - math.log(calib["c_ultima"])) / esc
    q = calib["q"] + incl
    qp = calib["qp"] + incl * (np.arange(1, H + 1) / H)[:, None]
    # la probabilidad de subida se desplaza con la inclinación (±0,35 σ ≈ ±10 puntos)
    p_up = min(.95, max(.05, calib["p_up"] + incl * .3))
    med = float(q[2])
    if p_up >= .56 and med > .05:
        dirc = "alcista"
    elif p_up <= .44 and med < -.05:
        dirc = "bajista"
    else:
        dirc = "lateral"
    p_dir = p_up if dirc == "alcista" else (1 - p_up) if dirc == "bajista" else None
    val = calib.get("val")
    conf = "baja"
    if dirc != "lateral":
        ok = bool(val and val["destreza"] > .02 and (val["acierto"] or 0) >= .55 and val["llamadas"] >= 8)
        if p_dir >= .6 and ok:
            conf = "alta"
        elif p_dir >= .55 and (ok or calib["fiable"]):
            conf = "media"
    px = lambda v: float(price * math.exp(v * esc))  # noqa: E731
    centro = px(q[2])
    out = {"tf": tf, "velas": H, "horas": calib["horas"], "dir": dirc, "conf": conf, "fiable": calib["fiable"],
           "p_up": float(p_up), "p_dir": None if p_dir is None else float(p_dir), "centro": centro,
           "b50": [px(q[1]), px(q[3])], "b80": [px(q[0]), px(q[4])], "sd_pct": esc * 100,
           "q": {str(int(a * 100)): [px(v) for v in qp[:, j]] for j, a in enumerate(QS)},
           "analogos": [{"t": a["t"], "ret": [(math.exp(r * esc) - 1) * 100 for r in a["ret"]], "fin": (math.exp(a["fin"] * esc) - 1) * 100, "peso": a.get("peso")} for a in calib["analogos"]],
           "val": val, "n": calib["muestras"], "k": calib["k"], "n_eff": round(calib["n_eff"], 1), "rasgos": calib["rasgos"],
           "inclinacion": nt, "recorrido": d, "nivel": None, "t_calib": calib["t_ultima"], "score21": calib.get("score21"),
           "lam": calib["lam"], "p_up_ana": calib["p_up_ana"]}
    if z:
        if dirc == "bajista":
            s = next((g for g in z.get("sup", []) if not g.get("proj") and g["mid"] >= centro and g["mid"] < price), None)
            if s:
                out["nivel"] = {"tipo": "soporte", "precio": s["mid"], "fuerza": s.get("s", 0)}
        elif dirc == "alcista":
            r = next((g for g in z.get("res", []) if not g.get("proj") and g["mid"] <= centro and g["mid"] > price), None)
            if r:
                out["nivel"] = {"tipo": "resistencia", "precio": r["mid"], "fuerza": r.get("s", 0)}
    return out


def compacto(p):
    """Versión ligera (sin recorridos ni análogos) para enviar cada segundo a todas las monedas."""
    if not p:
        return None
    return {k: v for k, v in p.items() if k not in ("q", "analogos", "rasgos", "val")}
