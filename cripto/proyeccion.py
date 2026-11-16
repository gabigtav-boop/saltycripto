"""Proyección por volatilidad: el rango probable del precio y la probabilidad de llegar a cada nivel.

Por qué así (estudio completo en ANALISIS_PREDICCION.md): con 8 monedas, casi 4 años y unas 19.000 pruebas fuera de muestra,
ningún método basado en precio, volumen, flujo de órdenes, funding, base de futuros o miedo y codicia (analogías, regresión
de cresta, árboles de decisión) acertó la dirección mejor que lanzar una moneda, tampoco en el mercado bajista de 2022, y
ninguno batió a comprar y mantener al descontar costes. Lo que sí es predecible es la volatilidad, que agrupa en rachas.
Por eso este módulo no afirma dirección: dice cuánto puede moverse el precio y con qué probabilidad tocará cada nivel.

1) Volatilidad. Una sola estimación por vela: mezcla de la volatilidad reciente (media móvil exponencial de 20 velas) y la
   de largo plazo (100 velas). Se usa igual en todas las temporalidades, así los rangos de 1 h, 4 h y 1 d son coherentes.
2) Rango. Para cada vela del histórico se mide el retorno de las H velas siguientes dividido por la volatilidad del momento.
   Los cuantiles de esa distribución (sin deriva: mediana en cero) multiplicados por la volatilidad de hoy dan el abanico.
3) Niveles. Con el mismo histórico se mide la mayor excursión (máximo y mínimo) de las H velas siguientes, también en
   unidades de volatilidad. La probabilidad de tocar un nivel a una distancia d es la frecuencia histórica de excursiones
   de al menos d / volatilidad actual.
4) Casos. Episodios reales del histórico con volatilidad y tendencia parecidas a las de ahora, dibujados a la escala de la
   volatilidad de hoy. No sirven para saber hacia dónde irá el precio (los casos no añaden información sobre la dirección ni
   sobre la forma de la distribución: estudio 9 de ANALISIS_PREDICCION.md) sino para ver cómo se mueve en situaciones así.
   Para que no engañen: se buscan 50 (no los 4 más parecidos, cuya dispersión es solo un 70 % de la real y aparentan una
   coherencia que no existe) y se dibujan 5 elegidos a los percentiles 10-25-50-75-90 del resultado. Con esa elección
   reproducen la dispersión real, y se comprueba fuera de muestra. No se dibujan más (la nube tapaba el gráfico).
5) Validación fuera de muestra. Sobre el último 35 % del histórico y con puntos separados por el horizonte, se mide la
   cobertura real del rango (50 % y 80 %) y cuánto mejoran las probabilidades de tocar niveles frente a ignorar la
   volatilidad (puntuación de Brier). Todo con velas cerradas: ninguna muestra usa información posterior a su instante."""
import math

import numpy as np

HOR = {"1m": 60, "1h": 24, "4h": 12, "1d": 7, "1w": 4}         # velas que se proyectan
HORAS = {"1m": 1 / 60, "1h": 1, "4h": 4, "1d": 24, "1w": 168}   # horas por vela
WARM = 100
MIN_MUESTRAS = 60
QS = (.1, .25, .5, .75, .9)
ZG = np.linspace(0, 5, 101)                                      # malla de excursiones en unidades de volatilidad
MULT = (.5, 1.0, 1.5, 2.0)
KCASOS = 50                                                      # episodios reales que se buscan
WARM_C = 250                                                     # el nivel de volatilidad compara con las 250 velas anteriores                                       # distancias de prueba, en múltiplos de la volatilidad típica


# ---------- volatilidad ----------
def _ema(x, p):
    k = 2 / (p + 1)
    o = np.empty_like(x)
    e = x[:20].mean() if len(x) >= 20 else x.mean()
    for i in range(len(x)):
        e = x[i] * k + e * (1 - k)
        o[i] = e
    return o


R2_MAX = .5            # tope absoluto del retorno logarítmico al cuadrado que entra en la volatilidad (|r| ≈ 70 %)
CAP_REL = 100.0        # y tope relativo: un retorno no cuenta más de 100 veces la varianza reciente (10 volatilidades)


def _ema_robusta(r2, p):
    """Media móvil exponencial de retornos al cuadrado, robusta a datos absurdos: arranca con la mediana (no con la media)
    y cada retorno se recorta a CAP_REL veces la estimación de ese momento. Una mecha de 2016 en el histórico semanal de
    Kraken, o un dato erróneo, deja de arrastrar la volatilidad durante cientos de velas; una caída real de un 20 % sí
    sube la volatilidad (son decenas de volatilidades, pero el recorte deja pasar 10)."""
    k = 2 / (p + 1)
    e = max(float(np.median(r2[:20])) if len(r2) else 1e-10, 1e-10)
    o = np.empty_like(r2)
    for i in range(len(r2)):
        e = min(r2[i], R2_MAX, CAP_REL * e) * k + e * (1 - k)
        o[i] = e
    return o


def volatilidad(c):
    """Desviación típica del retorno logarítmico por vela: mezcla de memoria corta (20) y larga (100)."""
    lc = np.log(np.maximum(c, 1e-12))
    r2 = np.diff(lc, prepend=lc[0]) ** 2
    return np.sqrt(np.maximum(.5 * _ema_robusta(r2, 20) + .5 * _ema_robusta(r2, 100), 1e-14)), lc


def _ventanas(x, H, fn):
    """fn (max/min) de las H velas siguientes a cada vela (la última da NaN)."""
    n = len(x)
    out = np.full(n, np.nan)
    if n > H:
        v = np.lib.stride_tricks.sliding_window_view(x[1:], H)
        out[:len(v)] = fn(v, axis=1)
    return out


def _supervivencia(exc):
    """Probabilidad de que la excursión (en unidades de volatilidad) alcance cada punto de la malla ZG."""
    s = np.sort(exc)
    return 1 - np.searchsorted(s, ZG, side="left") / len(s)


def _cuantiles(y):
    q = np.quantile(y, QS)
    return q - q[2]                                              # sin deriva: la mediana es cero


def _interp(F, z):
    return float(np.interp(z, ZG, F))


# ---------- casos: episodios reales parecidos ----------
def _estado(c, h, l, sig, lc, H):
    """Rasgos de similitud sin escala: régimen y nivel de volatilidad, distancia a las medias de 50 y 200 velas (en ATR) e
    impulso del horizonte (en volatilidades). Todos con velas cerradas."""
    n = len(c)
    r2 = np.diff(lc, prepend=lc[0]) ** 2
    regimen = np.clip(np.log(np.sqrt(_ema_robusta(r2, 20)) / np.sqrt(_ema_robusta(r2, 100))) * 2, -3, 3)
    cs = np.cumsum(np.insert(sig, 0, 0.0))
    base = np.full(n, np.nan)
    if n > WARM_C:
        base[WARM_C:] = (cs[WARM_C:n] - cs[:n - WARM_C]) / WARM_C
    nivel = np.clip(np.log(sig / base), -2, 2)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.maximum(_ema(tr, 14), c * 1e-6)
    mom = (lc - np.concatenate([np.full(H, lc[0]), lc[:-H]])) / (sig * math.sqrt(H))
    return np.column_stack([regimen, nivel, np.clip((c - _ema(c, 50)) / atr, -8, 8),
                           np.clip((c - _ema(c, 200)) / atr, -12, 12) / 2, np.clip(mom, -4, 4)])


def _vecinos(Z, zq, tiempos, sep, k):
    """k vecinos más parecidos sin dos momentos a menos de `sep` velas (no se cuenta dos veces lo mismo)."""
    d2 = ((Z - zq) ** 2).sum(1)
    sel, bloq = [], np.zeros(int(tiempos.max()) + sep + 1, dtype=bool)
    for j in np.argsort(d2)[:14 * k]:
        tj = int(tiempos[j])
        if bloq[tj]:
            continue
        sel.append(int(j))
        bloq[max(0, tj - sep + 1):tj + sep] = True
        if len(sel) >= k:
            break
    return np.array(sel, dtype=int)


def _elegir(y_sel):
    """5 casos representativos: los que quedan a los percentiles 10-25-50-75-90 del resultado (de peor a mejor)."""
    o = np.argsort(y_sel)
    return [int(o[int(round(q * (len(o) - 1)))]) for q in QS]


def _casos(F, idx, y, lc, sig, t, H):
    """Busca los episodios parecidos al estado de la última vela cerrada."""
    X = F[idx]
    ok = np.isfinite(X).all(1)
    if ok.sum() < 150 or not np.isfinite(F[-1]).all():
        return None
    mu, sd = X[ok].mean(0), X[ok].std(0)
    sd[sd < 1e-9] = 1
    Z = np.where(np.isfinite(X), (X - mu) / sd, 1e3)               # lo que no tiene estado calculable no se parece a nada
    k = min(KCASOS, int(ok.sum() // 8))
    sel = _vecinos(Z, (F[-1] - mu) / sd, idx, max(1, H // 2), k)
    if len(sel) < 10:
        return None
    ii = idx[sel]
    ks = np.arange(1, H + 1)
    Zk = (lc[ii[:, None] + ks] - lc[ii][:, None]) / (sig[ii][:, None] * np.sqrt(ks))
    return {"t": t[ii].astype(np.int64), "y": y[sel], "Zk": Zk, "rep": _elegir(y[sel]), "k": int(len(sel))}


def _validar_casos(F, idx, y, H):
    """Fuera de muestra: ¿reproducen los casos la dispersión real? Último 35 %, un punto cada horizonte."""
    X = F[idx]
    ok = np.isfinite(X).all(1)
    m = len(idx)
    corte = int(m * .65)
    if ok[:corte].sum() < 150:
        return None
    mu, sd = X[:corte][ok[:corte]].mean(0), X[:corte][ok[:corte]].std(0)
    sd[sd < 1e-9] = 1
    Z = np.where(np.isfinite(X), (X - mu) / sd, 1e3)
    e50 = e80 = r80 = nt = 0
    razon = []
    for i in range(corte, m, H):
        if not ok[i]:
            continue
        lim = int(np.searchsorted(idx, idx[i] - H, side="right"))
        if lim < 150:
            continue
        sel = _vecinos(Z[:lim], Z[i], idx[:lim], max(1, H // 2), min(KCASOS, lim // 8))
        if len(sel) < 10:
            continue
        ys = y[sel]
        q = np.quantile(ys, QS)
        rep = ys[_elegir(ys)]
        e50 += q[1] <= y[i] <= q[3]
        e80 += q[0] <= y[i] <= q[4]
        r80 += rep[0] <= y[i] <= rep[-1]
        razon.append(ys.std() / (y[:lim].std() + 1e-9))
        nt += 1
    if nt < 12:
        return None
    return {"n": nt, "env50": e50 / nt, "env80": e80 / nt, "rep80": r80 / nt, "razon": float(np.mean(razon))}


def limpiar(rows):
    """Quita las velas con precios no finitos o no positivos y corrige máximos y mínimos incoherentes con apertura y cierre.
    No inventa nada: si falta una vela, la serie sigue siendo la misma sin ella."""
    out = []
    for r in rows:
        try:
            o, h, l, c, v = (float(r[k]) for k in ("o", "h", "l", "c", "v"))
        except (TypeError, ValueError, KeyError):
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c)) or min(o, h, l, c) <= 0:
            continue
        out.append({"t": r["t"], "o": o, "h": max(h, o, c, l), "l": min(l, o, c, h), "c": c, "v": v if math.isfinite(v) else 0.0})
    return out


# ---------- calibración (histórico) ----------
def calibrar(rows, tf, cache=None):
    """Prepara el histórico de una temporalidad: distribución de retornos y excursiones normalizadas por la volatilidad,
    y su validación fuera de muestra. `cache` se admite por compatibilidad y no se usa."""
    H = HOR[tf]
    rows = limpiar(rows)
    n = len(rows)
    if n < WARM + H + MIN_MUESTRAS:
        return None
    c = np.array([r["c"] for r in rows], dtype=float)
    hi = np.array([r["h"] for r in rows], dtype=float)
    lo = np.array([r["l"] for r in rows], dtype=float)
    sig, lc = volatilidad(c)
    idx = np.arange(WARM, n - H)
    if len(idx) < MIN_MUESTRAS:
        return None
    sH = sig[idx] * math.sqrt(H)
    y = (lc[idx + H] - lc[idx]) / sH
    up = np.maximum(np.log(_ventanas(hi, H, np.max)[idx] / c[idx]), 0) / sH
    dn = np.maximum(-np.log(_ventanas(lo, H, np.min)[idx] / c[idx]), 0) / sH
    # recorrido: en cada paso k, retorno acumulado normalizado por la volatilidad y la raíz de k (abanico)
    qp = np.empty((H, len(QS)))
    for k in range(1, H + 1):
        zk = (lc[idx + k] - lc[idx]) / (sig[idx] * math.sqrt(k))
        qp[k - 1] = _cuantiles(zk)
    qp[-1] = _cuantiles(y)
    m = len(idx)
    ref = float(np.median(sH))
    val = _validar(y, up, dn, sH, idx, H)
    activa = float(np.mean([r["v"] > 0 for r in rows[-500:]]))        # fracción de velas recientes con operaciones
    t = np.array([r["t"] for r in rows], dtype=np.int64)
    F = _estado(c, hi, lo, sig, lc, H)
    idx_c = np.arange(WARM + WARM_C, n - H)
    casos = val_casos = None
    if len(idx_c) >= 150:
        y_c = (lc[idx_c + H] - lc[idx_c]) / (sig[idx_c] * math.sqrt(H))
        casos = _casos(F, idx_c, y_c, lc, sig, t, H)
        val_casos = _validar_casos(F, idx_c, y_c, H)
    return {"casos": casos, "val_casos": val_casos, "activa": activa, "tf": tf, "H": H, "horas": H * HORAS[tf], "n": int(n), "muestras": int(m), "t_ultima": int(rows[-1]["t"]),
            "c_ultima": float(c[-1]), "sigma": float(sig[-1]), "q": _cuantiles(y), "qp": qp, "F_up": _supervivencia(up),
            "F_dn": _supervivencia(dn), "ref": ref, "val": val}


def _validar(y, up, dn, sH, idx, H):
    """Último 35 % del histórico, cada punto con lo conocido antes de él (el resultado de las muestras de entrenamiento
    ya había ocurrido) y puntos separados por el horizonte para que sean independientes."""
    m = len(y)
    corte = int(m * .65)
    pruebas = list(range(corte, m, H))
    c50 = c80 = nt = 0
    bm, bb = [], []
    cob = []
    for i in pruebas:
        lim = int(np.searchsorted(idx, idx[i] - H, side="right"))
        if lim < MIN_MUESTRAS:
            continue
        q = _cuantiles(y[:lim])
        cob.append((q[1] <= y[i] <= q[3], q[0] <= y[i] <= q[4]))
        ref = float(np.median(sH[:lim]))
        Fu, Fd = _supervivencia(up[:lim]), _supervivencia(dn[:lim])
        for mult in MULT:
            d = mult * ref
            for F, exc_tr, exc_i in ((Fu, up[:lim] * sH[:lim], up[i] * sH[i]), (Fd, dn[:lim] * sH[:lim], dn[i] * sH[i])):
                p_base = float(np.mean(exc_tr >= d))              # ignorando la volatilidad actual
                p_mod = _interp(F, d / sH[i])                      # según la volatilidad actual
                real = 1.0 if exc_i >= d else 0.0
                bm.append((p_mod - real) ** 2)
                bb.append((p_base - real) ** 2)
        nt += 1
    if nt < 12:
        return None
    cob = np.array(cob, dtype=float)
    return {"n": nt, "cob50": float(cob[:, 0].mean()), "cob80": float(cob[:, 1].mean()),
            "brier_mejora": float(1 - np.mean(bm) / np.mean(bb)) if np.mean(bb) > 0 else 0.0}


# ---------- proyección en vivo ----------
def p_tocar(calib, precio, nivel, lado):
    """Probabilidad de que el precio llegue a `nivel` dentro del horizonte. lado 'sube': el nivel está por encima."""
    esc = calib["sigma"] * math.sqrt(calib["H"])
    if lado == "sube":
        if precio >= nivel:
            return 1.0
        return _interp(calib["F_up"], math.log(nivel / precio) / esc)
    if precio <= nivel:
        return 1.0
    return _interp(calib["F_dn"], math.log(precio / nivel) / esc)


def proyectar(price, tf, calib, prev=None, z=None):
    """Convierte la calibración en precios desde el precio actual: abanico, rango y probabilidad de tocar los niveles.
    Es instantáneo (se llama cada segundo). `prev` se admite por compatibilidad y no inclina nada: sin ventaja demostrada."""
    if not calib or not price or price <= 0:
        return None
    H = calib["H"]
    sig = calib["sigma"]
    esc = sig * math.sqrt(H)
    q = calib["q"]
    px = lambda v, e=esc: float(price * math.exp(v * e))  # noqa: E731
    out = {"tf": tf, "velas": H, "horas": calib["horas"], "dir": "lateral", "conf": "baja", "fiable": False,
           "centro": float(price), "b50": [px(q[1]), px(q[3])], "b80": [px(q[0]), px(q[4])], "sd_pct": esc * 100,
           "q": {str(int(a * 100)): [float(price * math.exp(calib["qp"][k, j] * sig * math.sqrt(k + 1))) for k in range(H)]
                 for j, a in enumerate(QS)},
           "val": calib["val"], "n": calib["muestras"], "t_calib": calib["t_ultima"], "activa": calib.get("activa"), "niveles": []}
    cs = calib.get("casos")
    if cs:
        ks = np.arange(1, H + 1)
        sel = list(cs["rep"])
        ret = (np.exp(cs["Zk"][sel] * (sig * np.sqrt(ks))) - 1) * 100
        out["analogos"] = [{"t": int(cs["t"][j]), "ret": [round(float(x), 3) for x in ret[i]], "fin": float(ret[i][-1]),
                            "rep": i + 1} for i, j in enumerate(sel)]
        fin = (np.exp(cs["y"] * esc) - 1) * 100
        out["casos"] = {"k": cs["k"], "sube": int((cs["y"] > 0).sum()), "baja": int((cs["y"] <= 0).sum()),
                        "b50": [float(np.percentile(fin, 25)), float(np.percentile(fin, 75))]}
    out["val_casos"] = calib.get("val_casos")
    if z:
        for clave, lado, pref in (("res", "sube", "S"), ("sup", "baja", "E")):
            for i, g in enumerate(z.get(clave, [])[:2]):
                borde = g["lo"] if lado == "sube" else g["hi"]            # el primer contacto es el borde más cercano al precio
                p = p_tocar(calib, price, borde, lado)
                out["niveles"].append({"id": f"{pref}{i + 1}", "lado": lado, "precio": float(g["mid"]), "borde": float(borde),
                                       "pct": float((g["mid"] / price - 1) * 100), "p": float(p), "proj": bool(g.get("proj"))})
    return out


def compacto(p):
    """Versión ligera (sin los recorridos del abanico ni los casos) para enviar cada segundo a todas las monedas."""
    if not p:
        return None
    return {k: v for k, v in p.items() if k not in ("q", "analogos")}
