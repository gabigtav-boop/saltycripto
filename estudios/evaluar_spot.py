"""Evaluación prospectiva por activo de la decisión spot «Esperar una zona». Lee la base sin modificarla.

Para cada decisión «Esperar» con zona (la primera de cada activo y hora, para que una moneda que cambia mucho de señal no
pese más) se comparan tres alternativas con la MISMA salida (el precio al cumplirse 24 h) y el MISMO coste por lado:
  · espera    orden límite en el borde superior de la zona durante 24 h: compra en el primer tramo de 15 min con
              operaciones cuyo mínimo llega a la zona, al menor entre su apertura y ese borde; si no llega, efectivo (0 %)
  · inmediata compra al precio de la decisión
  · repartida mitad al precio de la decisión y mitad a las 12 h
Precios: velas de 15 min de Kraken REST (mismo par, `velas_15m_rest`), que se descargan con los últimos 7,5 días cada vez
que la app está abierta: basta con abrirla un rato al día. Salida = apertura del primer tramo que empieza a las 24 h o
después. Se excluyen, y se cuentan por motivo, los casos que no pueden evaluarse."""
import argparse
import collections
import json
import pathlib
import sqlite3
import statistics
import time

MIN = 60_000
Q = 15 * MIN
DIA = 1440 * MIN
ALCANCE = 7 * DIA                 # Kraken REST da 7,5 días de velas de 15 min: un tramo más antiguo ya no se puede descargar
RUTA = pathlib.Path(__file__).resolve().parents[1] / "datos" / "decisiones.sqlite3"
MOTIVOS = {"repetida": "repetida en la misma hora", "otra": "otra decisión (no «Esperar»)", "sin_zona": "«Esperar» sin zona",
           "pendiente": "pendientes de 24 h", "datos": "esperando velas de 15 min", "hueco": "sin seguimiento suficiente"}


def evaluar(db, ahora, coste):
    """Devuelve (motivos de exclusión y pendientes, {activo: [(zona tocada, espera, inmediata, repartida), …]})."""
    grupos = collections.defaultdict(list)
    estados = collections.Counter()
    vistos = set()
    hay15 = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='velas_15m_rest'").fetchone())
    for sym, t, raw in db.execute("SELECT simbolo,t,detalle FROM decisiones ORDER BY t").fetchall():
        d = json.loads(raw)
        # primero se filtra y luego se toma la primera «Esperar con zona» de cada hora: antes, si la primera decisión de la
        # hora era otra (p. ej. «ND» al arrancar), la espera válida de esa hora se descartaba como repetida
        if d.get("accion") != "E":
            estados[MOTIVOS["otra"]] += 1
            continue
        if not d.get("zona"):
            estados[MOTIVOS["sin_zona"]] += 1
            continue
        k = (sym, t // (60 * MIN))
        if k in vistos:
            estados[MOTIVOS["repetida"]] += 1
            continue
        vistos.add(k)
        if ahora < t + DIA + Q + 2 * MIN:
            estados[MOTIVOS["pendiente"]] += 1
            continue
        q0, qf, qm = -(-t // Q) * Q, -(-(t + DIA) // Q) * Q, -(-(t + DIA // 2) // Q) * Q
        v = {} if not hay15 else {r[0]: r[1:] for r in db.execute(
            "SELECT t,o,h,l,c,n FROM velas_15m_rest WHERE simbolo=? AND t>=? AND t<=? ORDER BY t", (sym, q0, qf))}
        if any(q not in v for q in range(q0, qf + Q, Q)):
            estados[MOTIVOS["datos" if ahora - q0 < ALCANCE else "hueco"]] += 1
            continue
        lo, hi = d["zona"]
        entrada = next((min(v[q][0], hi) for q in range(q0, qf, Q) if (v[q][4] or 0) > 0 and v[q][2] <= hi), None)
        fin, mitad = v[qf][0], v[qm][0]

        def neto(precio):
            return fin * (1 - coste) / (precio * (1 + coste)) - 1        # misma salida y mismo coste por lado
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
            "metodo": "Misma salida (apertura del tramo de 15 min de las 24 h) y mismo coste por lado para las tres "
                      "alternativas; observaciones horarias solapadas; velas de 15 min de Kraken REST."}


# =====================================================================================================================
#  eval-spot/2 · comprar, esperar y revisar salida (versión de estabilidad, 20 nov 2026)
#  El evaluador anterior (evaluar/informe, «esperas-v1») se conserva tal cual: su resultado sobre la base auditada
#  (63 esperas) está congelado en estudios/referencia_esperas_v1.json bajo su supuesto original.
# =====================================================================================================================
import bisect
import hashlib
import threading

HORA = 60 * MIN
EVALUADOR = "eval-spot/2"
REGLA = "primer-ask-bid-posterior/1"
TOL_LIBRO = 6 * MIN                 # el libro verificado se fotografía cada 5 min: tolerancia temporal explícita
HORIZONTES = (24, 168)              # 24 h obligatorio; 7 días solo si el archivo local cubre la ventana entera
IMPORTE_REF = 100.0                 # presupuesto de referencia en euros (los resultados se expresan en %)
ESCENARIOS = {
    "hist-0.10": {"maker": .001, "taker": .001, "etiqueta": "Comparador histórico: 0,10 % por lado"},
    "estudio-0.40-0.80": {"maker": .004, "taker": .008,
                          "etiqueta": "Escenario del estudio (20 nov 2026): 0,40 % maker / 0,80 % taker; tu tarifa depende del par y del nivel"},
}
SITUACIONES = {"C": ("C_efectivo",), "E": ("E_zona",), "E0": ("E_sin_zona",), "V": ("V_posicion", "V_efectivo")}
ALTERNATIVAS = {
    "C_efectivo": ("comprar", "euros", "repartir"),
    "E_zona": ("esperar_limite", "comprar", "repartir"),
    "E_sin_zona": ("euros", "comprar"),
    "V_posicion": ("vender", "mantener"),
    "V_efectivo": ("evitar", "comprar"),
}
NOMBRES = {"comprar": "comprar al primer ask posterior", "euros": "continuar en euros", "repartir": "mitad ahora y mitad a mitad del horizonte",
           "esperar_limite": "orden límite en el borde superior de la zona", "vender": "vender al primer bid posterior y quedar en euros",
           "mantener": "mantener las mismas unidades", "evitar": "evitar la entrada (euros)",
           "C_efectivo": "C con efectivo", "E_zona": "E con zona", "E_sin_zona": "E sin zona", "V_posicion": "V con posición (hipotética)",
           "V_efectivo": "V desde efectivo"}
METODO = (
    "Convención causal: cada alternativa empieza después de la emisión registrada. Compra inmediata al primer ask del libro "
    "verificado posterior (tolerancia 6 min); venta al primer bid posterior. Si no hay libro en esa tolerancia se usa la "
    "apertura del primer tramo de 15 min que empieza en la emisión o después, y el resultado se etiqueta como aproximación. "
    "Nunca se usa el mínimo de una vela que empezó antes de la emisión. La orden límite de «E con zona» es una simulación "
    "(libro incompleto): un toque no es una ejecución acreditada; si cruza el libro al ponerla paga taker. Todas las "
    "alternativas usan el mismo presupuesto (comisión incluida), el mismo vencimiento y el mismo escenario de costes; la "
    "venta final solo se descuenta a las alternativas que conservan monedas. Las alternativas de una misma emisión "
    "comparten el mismo movimiento de mercado (comparación pareada); las ventanas de emisiones cercanas se solapan y los "
    "activos de una misma ventana comparten el movimiento común del mercado cripto. «Independientes» = selección "
    "cronológica por activo, situación y horizonte: se toma la primera emisión evaluada y la siguiente solo si empieza "
    "cuando vence la anterior. Ninguna alternativa combina stop y objetivo, así que no hay ambigüedad de orden dentro de "
    "una vela. Las medias son simulaciones por observación, no rentabilidad esperada.")
ESQUEMA_EVAL = """
CREATE TABLE IF NOT EXISTS escenarios_spot (id TEXT PRIMARY KEY, config TEXT NOT NULL, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS evaluacion_spot (escenario TEXT NOT NULL, simbolo TEXT NOT NULL, t INTEGER NOT NULL,
  situacion TEXT NOT NULL, horizonte_h INTEGER NOT NULL, estado TEXT NOT NULL, categoria TEXT NOT NULL, detalle TEXT NOT NULL,
  t_eval INTEGER NOT NULL, PRIMARY KEY (escenario, simbolo, t, situacion, horizonte_h)) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS evaluacion_spot_inmutable BEFORE UPDATE ON evaluacion_spot
  BEGIN SELECT RAISE(ABORT, 'evaluacion_spot es inmutable: un cambio de criterio crea otro escenario'); END;
"""
_CERROJO = threading.Lock()


def escenario_id(maker, taker):
    """Identificador estable: un cambio de coste o de regla crea otro escenario y nunca reescribe lo ya publicado."""
    return f"{EVALUADOR}|{REGLA}|maker={maker:.5f}|taker={taker:.5f}"


def _ceil(t, paso=Q):
    return -(-t // paso) * paso


class Mercado:
    """Precios ejecutables de un activo a partir de lo guardado: libro verificado (bid/ask cada 5 min) y velas de 15 min."""

    def __init__(self, db, sym):
        tablas = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.lib = [] if "libro" not in tablas else [r for r in db.execute(
            "SELECT t, bid, ask FROM libro WHERE simbolo=? AND t IS NOT NULL AND bid>0 AND ask>=bid ORDER BY t", (sym,))]
        self.lt = [r[0] for r in self.lib]
        self.v = {} if "velas_15m_rest" not in tablas else {r[0]: r[1:] for r in db.execute(
            "SELECT t,o,h,l,c,n FROM velas_15m_rest WHERE simbolo=? ORDER BY t", (sym,))}

    def ejecutable(self, t, lado):
        """(precio, fuente, hora) del primer ask (compra) o bid (venta) posterior a t; None si no hay dato."""
        i = bisect.bisect_left(self.lt, t)
        if i < len(self.lt) and self.lt[i] - t <= TOL_LIBRO:
            r = self.lib[i]
            return (r[2] if lado == "ask" else r[1]), "libro", r[0]
        q = _ceil(t)
        if q in self.v and self.v[q][0]:
            return self.v[q][0], "apertura_15m", q
        return None

    def cubre(self, q0, qf):
        return all(q in self.v for q in range(q0, qf + Q, Q))


def _liq(p_in, f_in, p_out, f_out):
    """Rendimiento de comprar con un presupuesto (comisión incluida) y liquidar al vencimiento."""
    return p_out * (1 - f_out) / (p_in * (1 + f_in)) - 1


def evaluar_caso(m, t, situacion, zona, horizonte_h, maker, taker, ahora):
    """Devuelve (estado, detalle). estado: evaluada · pendiente · datos (esperando datos descargables) · sin_seguimiento."""
    T = t + horizonte_h * HORA
    if ahora < T + Q + 2 * MIN + TOL_LIBRO:
        return "pendiente", {"motivo": f"pendiente de {horizonte_h} h"}
    q0, qT = _ceil(t), _ceil(T)
    if horizonte_h > 24 and not m.cubre(q0, qT):
        return "sin_seguimiento", {"motivo": "el archivo local no cubre íntegramente la ventana de 7 días"}
    if situacion == "E_zona" and not m.cubre(q0, qT):
        return ("datos" if ahora - q0 < ALCANCE else "sin_seguimiento"), {"motivo": "faltan velas de 15 min en la ventana"}
    a0, b0, bT = m.ejecutable(t, "ask"), m.ejecutable(t, "bid"), m.ejecutable(T, "bid")
    aM = m.ejecutable(t + horizonte_h * HORA // 2, "ask")
    if not (a0 and b0 and bT and aM):
        return ("datos" if ahora - q0 < ALCANCE else "sin_seguimiento"), {"motivo": "falta un precio ejecutable al inicio, a mitad o al vencimiento"}
    fuentes = {a0[1], b0[1], bT[1], aM[1]}
    comprar = _liq(a0[0], taker, bT[0], taker)
    repartir = (comprar + _liq(aM[0], taker, bT[0], taker)) / 2
    alt, extra = {}, {}
    if situacion == "C_efectivo":
        alt = {"comprar": comprar, "euros": 0.0, "repartir": repartir}
    elif situacion == "E_zona":
        lo, hi = zona
        if a0[0] <= hi:              # la orden cruza el libro al ponerla: se ejecuta como taker al ask
            entrada, fee, via = a0[0], taker, "cruza el libro: taker"
        else:
            entrada, fee, via = None, maker, None
            for q in range(q0, qT, Q):
                o, h, l, c, n = m.v[q]
                if (n or 0) > 0 and l <= hi:
                    entrada, via = min(o, hi), "toque simulado en vela de 15 min (no es ejecución acreditada)"
                    break
        alt = {"esperar_limite": _liq(entrada, fee, bT[0], taker) if entrada else 0.0, "comprar": comprar, "repartir": repartir}
        extra = {"zona_tocada": entrada is not None, "via": via or "sin toque: conserva efectivo", "zona": [lo, hi]}
    elif situacion == "E_sin_zona":
        alt = {"euros": 0.0, "comprar": comprar}
    elif situacion == "V_posicion":
        alt = {"vender": -taker, "mantener": bT[0] / b0[0] * (1 - taker) - 1}
    elif situacion == "V_efectivo":
        alt = {"evitar": 0.0, "comprar": comprar}
    det = {"alternativas": alt, "precios": {"ask_0": a0[:2], "bid_0": b0[:2], "ask_mitad": aM[:2], "bid_fin": bT[:2]},
           "aproximacion": "apertura_15m" in fuentes, **extra}
    return "evaluada", det


def _candidatos(db, ahora, par_catalogo):
    """Lee los registros y los clasifica. Devuelve (conteo, nd, candidatos[(sym, t, accion, zona, categoria)])."""
    conteo = collections.Counter()
    excl = collections.Counter()
    nd = collections.defaultdict(lambda: {"n": 0, "horas": 0.0})
    vistos, cand = set(), []
    filas = db.execute("SELECT simbolo,t,detalle FROM decisiones ORDER BY simbolo,t").fetchall()
    for i, (sym, t, raw) in enumerate(filas):
        conteo["emisiones"] += 1
        d = json.loads(raw)
        if d.get("sintetico"):
            excl["sintético (prueba de software)"] += 1
            continue
        cat = d.get("esquema") or "legado"
        conteo["cat:" + cat] += 1
        acc = d.get("accion")
        if acc == "ND":
            sig = filas[i + 1][1] if i + 1 < len(filas) and filas[i + 1][0] == sym else ahora
            x = nd[d.get("motivo") or "sin motivo"]
            x["n"] += 1
            x["horas"] += min(max(sig - t, 0), HORA) / HORA        # tiempo sin cobertura, sin imputar ningún retorno
            continue
        if acc not in ("C", "E", "V"):
            excl["acción desconocida"] += 1
            continue
        par = d.get("par")
        if par and par_catalogo.get(sym) and par != par_catalogo[sym]:
            excl["mercado distinto del catálogo actual"] += 1
            continue
        clave = "E" if acc == "E" and d.get("zona") else "E0" if acc == "E" else acc
        k = (sym, clave, t // HORA)
        if k in vistos:
            excl["repetida en la misma hora"] += 1
            continue
        vistos.add(k)
        conteo["candidatas"] += 1
        cand.append((sym, t, clave, d.get("zona"), cat))
    return conteo, excl, {k: dict(v, horas=round(v["horas"], 2)) for k, v in nd.items()}, cand


def evaluar_v2(db, ahora, maker, taker, par_catalogo=None, guardados=None):
    """Evalúa todos los candidatos con un escenario de costes. `guardados` (clave → (estado, categoria, detalle)) son
    resultados ya publicados: se usan tal cual, sin recalcular."""
    par_catalogo = par_catalogo or {}
    guardados = guardados or {}
    conteo, excl, nd, cand = _candidatos(db, ahora, par_catalogo)
    mercados = {}
    filas = []
    for sym, t, clave, zona, cat in cand:
        m = mercados.get(sym) or mercados.setdefault(sym, Mercado(db, sym))
        for sit in SITUACIONES[clave]:
            for h in HORIZONTES:
                k = (sym, t, sit, h)
                if k in guardados:
                    est, cat_g, det = guardados[k]
                    filas.append((sym, t, sit, h, est, cat_g, det, True))
                    continue
                est, det = evaluar_caso(m, t, sit, zona, h, maker, taker, ahora)
                filas.append((sym, t, sit, h, est, cat, det, False))
    return conteo, excl, nd, filas


def resumir(conteo, excl, nd, filas, maker, taker):
    grupos = {}
    for sym, t, sit, h, est, cat, det, _ in filas:
        g = grupos.setdefault((sym, sit, h), {"simbolo": sym, "situacion": sit, "horizonte_h": h, "candidatas": 0,
                                              "evaluadas": 0, "pendientes": 0, "esperando_datos": 0, "sin_seguimiento": 0,
                                              "legado": 0, "aproximadas": 0, "zona_tocada": 0, "_ev": []})
        g["candidatas"] += 1
        if est == "evaluada":
            g["evaluadas"] += 1
            g["legado"] += cat == "legado"
            g["aproximadas"] += bool(det.get("aproximacion"))
            g["zona_tocada"] += bool(det.get("zona_tocada"))
            g["_ev"].append((t, det["alternativas"]))
        else:
            g[{"pendiente": "pendientes", "datos": "esperando_datos"}.get(est, "sin_seguimiento")] += 1
    out = []
    for g in grupos.values():
        ev = sorted(g.pop("_ev"))
        indep, fin = [], -1
        for t, a in ev:
            if t >= fin:
                indep.append(a)
                fin = t + g["horizonte_h"] * HORA
        g["independientes"] = len(indep)
        alts = ALTERNATIVAS[g["situacion"]]
        g["alternativas"] = {a: {"media": statistics.mean(x[a] for _, x in ev) if ev else None,
                                 "media_independientes": statistics.mean(x[a] for x in indep) if indep else None} for a in alts}
        out.append(g)
    out.sort(key=lambda g: (g["situacion"], g["simbolo"], g["horizonte_h"]))
    compras = sum(g["evaluadas"] for g in out if g["situacion"] == "C_efectivo")
    return {"evaluador": EVALUADOR, "regla": REGLA, "tolerancia_libro_min": TOL_LIBRO // MIN, "importe_ref": IMPORTE_REF,
            "escenario": {"id": escenario_id(maker, taker), "maker": maker, "taker": taker},
            "horizontes": list(HORIZONTES),
            "conteo": {"emisiones": conteo["emisiones"], "candidatas": conteo["candidatas"],
                       "evaluadas": sum(g["evaluadas"] for g in out), "pendientes": sum(g["pendientes"] for g in out),
                       "esperando_datos": sum(g["esperando_datos"] for g in out),
                       "sin_seguimiento": sum(g["sin_seguimiento"] for g in out),
                       "por_categoria": {k[4:]: v for k, v in conteo.items() if k.startswith("cat:")}},
            "excluidas": dict(excl), "nd": nd, "grupos": out,
            "compras": {"evaluadas": compras, "mensaje": None if compras else "Sin casos de compra evaluables"},
            "metodo": METODO, "nombres": NOMBRES}


def _guardados(db, esc_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='evaluacion_spot'").fetchone():
        return {}
    return {(s, t, sit, h): (est, cat, json.loads(det)) for s, t, sit, h, est, cat, det in db.execute(
        "SELECT simbolo,t,situacion,horizonte_h,estado,categoria,detalle FROM evaluacion_spot WHERE escenario=?", (esc_id,))}


def informe_v2(db, ahora, maker, taker, par_catalogo=None):
    """Solo lectura: lo publicado se lee tal cual y lo nuevo se calcula al vuelo."""
    esc = escenario_id(maker, taker)
    r = evaluar_v2(db, ahora, maker, taker, par_catalogo, _guardados(db, esc))
    return resumir(*r, maker, taker)


def migrar(db):
    """Tablas de la evaluación spot. Idempotente y en una transacción: las tablas existentes no se tocan."""
    db.execute("BEGIN IMMEDIATE")
    try:
        for sql in [s for s in ESQUEMA_EVAL.split(";\n") if s.strip()]:
            db.execute(sql)
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def registrar(ruta, ahora, par_catalogo=None, escenarios=None):
    """Evalúa y guarda los resultados definitivos (evaluada / sin seguimiento) de cada escenario. Idempotente: dos
    evaluadores a la vez, un reinicio o una segunda ejecución no duplican nada (clave primaria + INSERT OR IGNORE)
    y lo ya publicado no se reescribe (disparador). Devuelve {id_escenario: resumen}."""
    out = {}
    with _CERROJO, sqlite3.connect(ruta, timeout=10, isolation_level=None) as db:
        migrar(db)
        for nombre, e in (escenarios or ESCENARIOS).items():
            esc = escenario_id(e["maker"], e["taker"])
            db.execute("INSERT OR IGNORE INTO escenarios_spot VALUES (?,?,?)",
                       (esc, json.dumps({"nombre": nombre, **e, "evaluador": EVALUADOR, "regla": REGLA,
                                         "tolerancia_libro_min": TOL_LIBRO // MIN, "horizontes": HORIZONTES,
                                         "importe_ref": IMPORTE_REF}, ensure_ascii=False), int(ahora)))
            r = evaluar_v2(db, ahora, e["maker"], e["taker"], par_catalogo, _guardados(db, esc))
            nuevas = [(esc, s, t, sit, h, est, cat, json.dumps(det, ensure_ascii=False, allow_nan=False, separators=(",", ":")), int(ahora))
                      for s, t, sit, h, est, cat, det, ya in r[3] if not ya and est in ("evaluada", "sin_seguimiento")]
            db.execute("BEGIN IMMEDIATE")
            try:
                db.executemany("INSERT OR IGNORE INTO evaluacion_spot VALUES (?,?,?,?,?,?,?,?,?)", nuevas)
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
            out[esc] = dict(resumir(*evaluar_v2(db, ahora, e["maker"], e["taker"], par_catalogo, _guardados(db, esc)),
                                    e["maker"], e["taker"]), nombre=nombre, etiqueta=e["etiqueta"])
    return out


def huella_resultados(r):
    """Huella del contenido de un resumen (sin horas de generación) para comparar ejecuciones."""
    return hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def referencia_v1():
    p = pathlib.Path(__file__).with_name("referencia_esperas_v1.json")
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coste", type=float, default=.001, help="Coste por lado del evaluador original de esperas, p. ej. 0.001")
    ap.add_argument("--maker", type=float, default=None, help="Comisión maker del escenario nuevo, p. ej. 0.004")
    ap.add_argument("--taker", type=float, default=None, help="Comisión taker del escenario nuevo, p. ej. 0.008")
    args = ap.parse_args()
    for v in (args.coste, args.maker, args.taker):
        if v is not None and not 0 <= v < 1:
            ap.error("Las comisiones deben estar entre 0 y 1")
    if not RUTA.exists():
        print("Aún no hay decisiones. Abre SaltyCripto para empezar a registrarlas.")
        return
    ahora = time.time() * 1000
    with sqlite3.connect(RUTA.as_uri() + "?mode=ro", uri=True) as db:
        r = informe(db, ahora, args.coste)
        escs = ({"usuario": {"maker": args.maker if args.maker is not None else args.taker,
                             "taker": args.taker if args.taker is not None else args.maker, "etiqueta": "Escenario indicado"}}
                if args.maker is not None or args.taker is not None else ESCENARIOS)
        v2 = {n: informe_v2(db, ahora, e["maker"], e["taker"]) for n, e in escs.items()}
    print("== Evaluador original de esperas (esperas-v1) ==")
    print(f"Coste simulado por lado: {args.coste:.2%}. Misma salida para las tres alternativas.")
    print(f"Decisiones registradas: {r['total']} · evaluadas: {r['evaluadas']}")
    for k, n in r["excluidas"].items():
        print(f"  excluidas · {k}: {n}")
    for sym, a in r["activos"].items():
        print(f"{sym}: {a['n']} observaciones · zona observada {a['zona_tocada']} · "
              f"espera {a['espera']:+.2%} · inmediata {a['inmediata']:+.2%} · "
              f"mitad ahora y mitad a 12 h {a['repartida']:+.2%}")
    ref = referencia_v1()
    if ref:
        print(f"Referencia congelada (base auditada): {ref['informe']['evaluadas']} esperas evaluadas.")
    for n, x in v2.items():
        c = x["conteo"]
        print(f"\n== {EVALUADOR} · escenario {n}: maker {x['escenario']['maker']:.2%} / taker {x['escenario']['taker']:.2%} ==")
        print(f"Emisiones {c['emisiones']} · candidatas {c['candidatas']} · evaluadas {c['evaluadas']} · pendientes {c['pendientes']} · "
              f"esperando datos {c['esperando_datos']} · sin seguimiento {c['sin_seguimiento']} · categorías {c['por_categoria']}")
        for k, v in x["excluidas"].items():
            print(f"  excluidas · {k}: {v}")
        for k, v in x["nd"].items():
            print(f"  ND · {k}: {v['n']} registros · {v['horas']} h sin cobertura (sin imputar retorno)")
        if x["compras"]["mensaje"]:
            print("  " + x["compras"]["mensaje"])
        for g in x["grupos"]:
            if not g["evaluadas"]:
                continue
            alts = " · ".join(f"{a} {v['media']:+.2%} (indep. {v['media_independientes']:+.2%})" for a, v in g["alternativas"].items())
            print(f"  {g['simbolo']} {NOMBRES[g['situacion']]} {g['horizonte_h']} h: {g['evaluadas']} evaluadas, "
                  f"{g['independientes']} independientes · {alts}")
    print("\nRendimientos medios simulados por observación; no son rentabilidad esperada.")


if __name__ == "__main__":
    main()
