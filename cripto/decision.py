"""Lectura spot experimental. El registro es local y contiene solo señales de mercado (la versión nube no lo usa: nube.py lo desactiva)."""
import json
import pathlib
import sqlite3
from contextlib import closing

VERSION = "nube-1"
FRESCO_MS = 120_000
HORA = 3_600_000
RUTA = pathlib.Path(__file__).resolve().parents[1] / "datos" / "decisiones.sqlite3"


def decision(o, ahora):
    prev = o.get("prev") or {}
    tm = o.get("tm") or {}
    zonas = (o.get("z") or {}).get("sup", [])
    zona = next((z for z in zonas if not z.get("proj")), {})
    t = o.get("precio_t") or 0
    if not t or not 0 <= ahora - t <= FRESCO_MS:
        accion, motivo = "ND", "Cotización pendiente de renovar"
    elif prev.get("obsoleta") or prev.get("cobertura", 0) < .5 or not tm:
        accion, motivo = "ND", "Análisis incompleto o pendiente de renovar"
    elif tm["v"] == "C" and prev["dir"] != "bajista":
        accion, motivo = "C", "Entrada técnica favorable y contexto compatible"
    elif tm["v"] == "V" and prev["dir"] != "alcista":
        accion, motivo = "V", "Se cumplen las condiciones técnicas de salida"
    elif (tm["v"], prev["dir"]) in (("C", "bajista"), ("V", "alcista")):
        accion, motivo = "E", "El contexto y el semáforo discrepan"
    else:
        accion, motivo = "E", "Esperar confirmación del semáforo"
    return dict(accion=accion, motivo=motivo, zona=[zona["lo"], zona["hi"]] if zona else None,
                cobertura=prev.get("cobertura"), precio_t=t, caduca=t + FRESCO_MS)


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
        detalle = dict(**d, version=VERSION, simbolo=sym, t=ahora, precio=o.get("price"),
                       fuente=o.get("precio_fuente"), semaforo=(o.get("tm") or {}).get("v"),
                       direccion=prev.get("dir"), faltan=prev.get("faltan"), factores=prev.get("factores"),
                       horizonte_h=24)
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
