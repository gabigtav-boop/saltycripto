"""Indicadores técnicos, traducción exacta de la v21 (JavaScript).

Las sumas se hacen con un bucle simple (izquierda a derecha) en lugar de sum():
desde Python 3.12 sum() usa suma compensada y daría resultados distintos al JS.
Las velas son dicts con t (ms), o, h, l, c, v.
"""
import math


def jsum(a):
    s = 0.0
    for x in a:
        s += x
    return s


def jdiv(a, b):
    """División con la semántica de JavaScript (x/0 = ±Infinity, 0/0 = NaN)."""
    if b == 0:
        if a == 0 or a != a:
            return math.nan
        return math.inf if (a > 0) == (math.copysign(1.0, b) > 0) else -math.inf
    return a / b


def clamp(x, a, b):
    return max(a, min(b, x))


def ema(a, p):
    k = 2 / (p + 1)
    e = a[0]
    o = [e]
    for i in range(1, len(a)):
        e = a[i] * k + e * (1 - k)
        o.append(e)
    return o


def rsi_s(c, p=14):
    n = len(c)
    o = [None] * n
    if n < p + 2:
        return o
    g = 0.0
    l = 0.0
    for i in range(1, p + 1):
        d = c[i] - c[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    g /= p
    l /= p
    o[p] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    for i in range(p + 1, n):
        d = c[i] - c[i - 1]
        g = (g * (p - 1) + max(d, 0)) / p
        l = (l * (p - 1) + max(-d, 0)) / p
        o[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    return o


def atr(r, p=14):
    if len(r) < p + 2:
        return None
    tr = [max(x["h"] - x["l"], abs(x["h"] - r[i]["c"]), abs(x["l"] - r[i]["c"])) for i, x in enumerate(r[1:])]
    a = jsum(tr[:p]) / p
    for i in range(p, len(tr)):
        a = (a * (p - 1) + tr[i]) / p
    return a


def adx(r, p=14):
    if len(r) < 2 * p + 2:
        return None
    tr, pd, md = [], [], []
    for i in range(1, len(r)):
        u = r[i]["h"] - r[i - 1]["h"]
        d = r[i - 1]["l"] - r[i]["l"]
        pd.append(u if (u > d and u > 0) else 0)
        md.append(d if (d > u and d > 0) else 0)
        tr.append(max(r[i]["h"] - r[i]["l"], abs(r[i]["h"] - r[i - 1]["c"]), abs(r[i]["l"] - r[i - 1]["c"])))
    t = jsum(tr[:p])
    P = jsum(pd[:p])
    M = jsum(md[:p])
    dx = []
    for i in range(p, len(tr) + 1):
        if i > p:
            t = t - t / p + tr[i - 1]
            P = P - P / p + pd[i - 1]
            M = M - M / p + md[i - 1]
        a = jdiv(100 * P, t)
        b = jdiv(100 * M, t)
        s = a + b
        if s == 0 or s != s:
            s = 1
        dx.append(100 * abs(a - b) / s)
    A = jsum(dx[:p]) / p
    for i in range(p, len(dx)):
        A = (A * (p - 1) + dx[i]) / p
    return A


def supertrend(r, p=10, m=3):
    n = len(r)
    if n < p + 2:
        return None
    tr = [max(x["h"] - x["l"], abs(x["h"] - r[i - 1]["c"]), abs(x["l"] - r[i - 1]["c"])) if i else x["h"] - x["l"]
          for i, x in enumerate(r)]
    a = jsum(tr[1:p + 1]) / p
    fu = fl = 0.0
    d = 1
    lv = 0.0
    for i in range(p, n):
        if i > p:
            a = (a * (p - 1) + tr[i]) / p
        hl = (r[i]["h"] + r[i]["l"]) / 2
        bu = hl + m * a
        bl = hl - m * a
        if i == p:
            fu, fl = bu, bl
        else:
            q = r[i - 1]["c"]
            fu = bu if (bu < fu or q > fu) else fu
            fl = bl if (bl > fl or q < fl) else fl
            if d == 1 and r[i]["c"] < fl:
                d = -1
            elif d == -1 and r[i]["c"] > fu:
                d = 1
        lv = fl if d == 1 else fu
    return {"dir": d, "lv": lv}


def piv(r, key, low, w=3):
    o = []
    for i in range(w, len(r) - w):
        ok = True
        a = r[i][key]
        for j in range(1, w + 1):
            if low:
                bad = a > r[i - j][key] or a > r[i + j][key]
            else:
                bad = a < r[i - j][key] or a < r[i + j][key]
            if bad:
                ok = False
                break
        if ok:
            o.append(i)
    return o


def mid(r, e, p):
    s = r[max(0, e - p + 1):e + 1]
    return (max(x["h"] for x in s) + min(x["l"] for x in s)) / 2


def bw_at(cl, e):
    w = cl[e - 19:e + 1]
    m = jsum(w) / 20
    return 4 * math.sqrt(jsum((x - m) ** 2 for x in w) / 20) / m


def holes(rows, minutes):
    r = rows[-100:]
    if len(r) < 50:
        return 0
    return 1 - (len(r) - 1) / ((r[-1]["t"] - r[0]["t"]) / (minutes * 60000))
