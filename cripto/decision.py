"""Lectura spot experimental. El registro es local y contiene solo señales de mercado (la versión nube no lo usa: nube.py lo desactiva)."""
import json
import pathlib
import sqlite3
from contextlib import closing

VERSION = "nube-1"
ESQUEMA = "registro-spot/2"
REGLA_EJECUCION = "primer-ask-bid-posterior/1"   # estudios/evaluar_spot.py::REGLA
IMPORTE_REF = 100.0
FRESCO_MS = 120_000
HORA = 3_600_000
RUTA = pathlib.Path(__file__).resolve().parents[1] / "datos" / "decisiones.sqlite3"


def decision(o, ahora, previa=None):
    """Acción spot de un activo. ND = no se puede evaluar (dato vencido, hora futura, cobertura < 0,50 o análisis sin
    recalcular), distinto de E = espera evaluada. `revisar` dice qué condición concreta de estas reglas haría cambiar la
    lectura. Si pasa a ND, `anterior` conserva la última lectura evaluada, identificada como anterior."""
    prev = o.get("prev") or {}
    tm = o.get("tm") or {}
    zonas = (o.get("z") or {}).get("sup", [])
    zona = next((z for z in zonas if not z.get("proj")), {})
    t = o.get("precio_t") or 0
    cob = prev.get("cobertura", 0) or 0
    if t and t > ahora:
        accion, motivo, revisar = "ND", "Hora de la cotización posterior a la actual: no es válida", "Se evaluará con una cotización de hora válida."
    elif not t or not 0 <= ahora - t <= FRESCO_MS:
        accion, motivo, revisar = "ND", "Cotización pendiente de renovar", "Se evaluará al recibir una cotización de menos de 120 s."
    elif prev.get("obsoleta") or cob < .5 or not tm:
        accion, motivo = "ND", "Análisis incompleto o pendiente de renovar"
        revisar = (f"Se evaluará cuando la cobertura de factores llegue al 50 % (ahora {round(cob * 100)} %)." if prev and cob < .5
                   else "Se evaluará cuando se recalcule el análisis con datos vigentes.")
    elif tm["v"] == "C" and prev["dir"] != "bajista":
        accion, motivo = "C", "Entrada técnica favorable y contexto compatible"
        revisar = "Cambia si el semáforo deja de indicar compra o la lectura combinada pasa a bajista."
    elif tm["v"] == "V" and prev["dir"] != "alcista":
        accion, motivo = "V", "Se cumplen las condiciones técnicas de salida"
        revisar = "Cambia si el semáforo deja de indicar venta o la lectura combinada pasa a alcista."
    elif (tm["v"], prev["dir"]) in (("C", "bajista"), ("V", "alcista")):
        accion, motivo = "E", "El contexto y el semáforo discrepan"
        revisar = ("Pasaría a «Valorar entrada» si la lectura combinada deja de ser bajista." if tm["v"] == "C"
                   else "Pasaría a «Revisar salida / Evitar entrada» si la lectura combinada deja de ser alcista.")
    else:
        accion, motivo = "E", "Esperar confirmación del semáforo"
        revisar = "Cambia si el semáforo pasa a compra (con contexto no bajista) o a venta (con contexto no alcista)."
    out = dict(accion=accion, motivo=motivo, zona=[zona["lo"], zona["hi"]] if zona else None,
               cobertura=prev.get("cobertura"), precio_t=t, caduca=t + FRESCO_MS, revisar=revisar)
    if accion == "ND" and previa:
        ant = previa if previa.get("accion") not in (None, "ND") else previa.get("anterior")
        if ant:
            out["anterior"] = {k: ant.get(k) for k in ("accion", "motivo", "precio_t", "zona")}
    return out


def _compacto(x):
    """Floats a 7 cifras significativas (como lo que se publica en la web): el registro ocupaba ~1,25 kB por decisión."""
    if isinstance(x, float):
        return float(f"{x:.7g}")
    if isinstance(x, dict):
        return {k: _compacto(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_compacto(v) for v in x]
    return x


class Registro:
    def __init__(self, ruta=RUTA):
        self.ruta = ruta
        self.ultima = {}
        self.minuto = {}
        self.ultimo_registro = 0
        self.ultima_acum = {}

    def guardar(self, sym, o, d, ahora):
        # Captura al cambiar de acción o motivo y en el primer ciclo de cada hora UTC.
        clave = (ahora // HORA, d["accion"], d["motivo"])
        nueva = self.ultima.get(sym) != clave
        cot = d["precio_t"] or 0
        muestra = (cot and 0 <= ahora - cot <= FRESCO_MS and
                   self.minuto.get(sym) != cot // 60000 and o.get("price", 0) > 0)
        if not nueva and not muestra:
            return
        prev = o.get("prev") or {}
        tk = o.get("tkv") or {}
        detalle = dict(**d, version=VERSION, simbolo=sym, t=ahora, precio=o.get("price"),
                       fuente=o.get("precio_fuente"), semaforo=(o.get("tm") or {}).get("v"),
                       direccion=prev.get("dir"), faltan=prev.get("faltan"), factores=prev.get("factores"),
                       horizonte_h=24,
                       # registro-spot/2: lo necesario para evaluar sin inventar nada (los registros sin estos campos
                       # se analizan como categoría «legado»); el coste no se fija aquí: cada escenario se evalúa aparte
                       esquema=ESQUEMA, par=(o.get("procedencia") or {}).get("par"), regla_ejecucion=REGLA_EJECUCION,
                       horizontes_h=[24, 168], importe_ref=IMPORTE_REF, modelo_costes="por escenario al evaluar",
                       precio_ref={"precio": o.get("price"), "precio_t": d.get("precio_t"), "bid": tk.get("bid"),
                                   "ask": tk.get("ask"), "ticker_t": tk.get("t")})
        with closing(sqlite3.connect(self.ruta, timeout=1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS decisiones (simbolo TEXT, t INTEGER, detalle TEXT, PRIMARY KEY(simbolo,t))")
            db.execute("CREATE TABLE IF NOT EXISTS precios (simbolo TEXT, minuto INTEGER, t INTEGER, precio REAL, PRIMARY KEY(simbolo,minuto))")
            if muestra:
                db.execute("INSERT OR IGNORE INTO precios VALUES (?,?,?,?)", (sym, cot // 60000, cot, o["price"]))
            if nueva:
                db.execute("INSERT OR IGNORE INTO decisiones VALUES (?,?,?)",
                       (sym, ahora, json.dumps(_compacto(detalle), allow_nan=False, ensure_ascii=False, separators=(",", ":"))))
        if muestra:
            self.minuto[sym] = cot // 60000
        if nueva:
            self.ultimo_registro = ahora
            self.ultima[sym] = clave

    def guardar_acum(self, sym, a, precio, ahora):
        """Registro prospectivo de las condiciones de acumulación: una foto por activo y día UTC, y al cambiar el estado."""
        if not a:
            return
        clave = (ahora // (24 * HORA), a["estado"])
        if self.ultima_acum.get(sym) == clave:
            return
        detalle = {"version": a["version"], "estado": a["estado"], "condiciones": {c["k"]: c["ok"] for c in a["condiciones"]},
                   "cumple": a["cumple"], "total": a["total"], "dd90": a.get("dd90"), "rsi": a.get("rsi"), "t_vela": a.get("t_vela")}
        with closing(sqlite3.connect(self.ruta, timeout=1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS acumulacion (simbolo TEXT, t INTEGER, estado TEXT, precio REAL, detalle TEXT, PRIMARY KEY(simbolo,t))")
            db.execute("INSERT OR IGNORE INTO acumulacion VALUES (?,?,?,?,?)",
                       (sym, ahora, a["estado"], precio, json.dumps(_compacto(detalle), allow_nan=False, ensure_ascii=False, separators=(",", ":"))))
        self.ultima_acum[sym] = clave
