"""Avisos trazables de BTC y ETH (se detectan en el servidor y se guardan en el SQLite de las decisiones).

1) Cruce de un nivel que sigue el usuario. Se mide con la última OPERACIÓN recibida por el WebSocket de Kraken del mismo par
   (no con el precio confirmado por el libro): un cruce exige que alguien haya negociado al otro lado del nivel.
   · Histéresis: el precio tiene que pasar ±0,1 % al otro lado del nivel; dentro de esa banda no cambia de lado, así que
     oscilar alrededor del nivel no genera avisos repetidos.
   · Enfriamiento: tras un aviso de un nivel, no se avisa de ese nivel en 15 min (el lado se sigue actualizando en silencio).
   · Continuidad: si entre dos observaciones válidas pasan más de 120 s (datos caducados, reconexión, reinicio), el lado se
     vuelve a tomar en silencio; si cambió durante el hueco se registra como «omitido» con su motivo, sin avisar.
2) Cambio de contexto sostenido: la decisión spot (Valorar entrada / Esperar / Evitar entrada) cambia y se mantiene
   PERIODO_CONTEXTO (5 min) seguidos con datos vigentes. Un dato caducado («ND») interrumpe la cuenta: no dispara nada.

Cada aviso guarda cuándo ocurrió el dato en la bolsa, cuándo llegó, cuándo se detectó la condición y cuándo se emitió, y el
contexto completo de ese momento. La entrega solo se registra cuando la página confirma que lo mostró."""
import json
import pathlib
import sqlite3
import threading
import time
from contextlib import closing

from .catalogo import SIMBOLOS
from .decision import RUTA

ACTIVOS = SIMBOLOS               # todas las monedas del catálogo
BANDA = .001
ENFRIA = 15 * 60_000
CONTINUIDAD = 120_000
PERIODO_CONTEXTO = 300_000
TXT = {"C": "Valorar entrada", "V": "Evitar entrada", "E": "Esperar"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS niveles_usuario (id INTEGER PRIMARY KEY AUTOINCREMENT, simbolo TEXT NOT NULL, precio REAL NOT NULL,
  nota TEXT, creado INTEGER NOT NULL, activo INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS estado_avisos (clave TEXT PRIMARY KEY, valor TEXT NOT NULL, t INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS avisos (id INTEGER PRIMARY KEY AUTOINCREMENT, clave TEXT UNIQUE NOT NULL, tipo TEXT NOT NULL,
  simbolo TEXT NOT NULL, estado TEXT NOT NULL, t_dato INTEGER, t_llegada INTEGER, t_deteccion INTEGER NOT NULL,
  t_emision INTEGER NOT NULL, t_entrega INTEGER, canal TEXT, titulo TEXT, texto TEXT, detalle TEXT);
"""


def eur(x):
    if x is None:
        return "—"
    d = 2 if x < 1000 else 1 if x < 10000 else 0
    return f"{x:,.{d}f}".replace(",", " ").replace(".", ",").replace(" ", ".") + " €"


class _Perezosa:
    """Conexión que se abre en la primera escritura."""

    def __init__(self, ruta):
        self.ruta, self.c = ruta, None

    def execute(self, *a):
        if self.c is None:
            self.c = sqlite3.connect(self.ruta, timeout=5)
        return self.c.execute(*a)

    def confirmar(self):
        if self.c is not None:
            self.c.commit()

    def cerrar(self):
        if self.c is not None:
            self.c.close()


def lado(p, nivel):
    if p >= nivel * (1 + BANDA):
        return "arriba"
    if p <= nivel * (1 - BANDA):
        return "abajo"
    return None


class Avisos:
    def __init__(self, ruta=RUTA, reloj=None):
        self.ruta = pathlib.Path(ruta)
        self.reloj = reloj or (lambda: int(time.time() * 1000))
        self.lock = threading.Lock()
        self.t_obs = {}             # clave -> última observación válida (solo en memoria: tras reiniciar hay hueco)
        self.cand = {}              # moneda -> (contexto candidato, desde)
        with self._db() as db, db:
            db.executescript(ESQUEMA)
            self.estado = {k: json.loads(v) for k, v in db.execute("SELECT clave, valor FROM estado_avisos")}
        self._niv = None

    def _db(self):
        return closing(sqlite3.connect(self.ruta, timeout=5))

    # ---------- niveles del usuario ----------
    def niveles(self, s=None):
        if self._niv is None:
            with self._db() as db:
                self._niv = [{"id": i, "simbolo": a, "precio": p, "nota": n, "creado": c}
                             for i, a, p, n, c in db.execute("SELECT id, simbolo, precio, nota, creado FROM niveles_usuario "
                                                             "WHERE activo=1 ORDER BY simbolo, precio")]
        return [x for x in self._niv if s is None or x["simbolo"] == s]

    def anadir_nivel(self, s, precio, nota, ahora):
        if s not in ACTIVOS or not isinstance(precio, (int, float)) or not 0 < precio < 1e9:
            raise ValueError("nivel no válido")
        with self.lock, self._db() as db, db:
            cur = db.execute("INSERT INTO niveles_usuario (simbolo, precio, nota, creado) VALUES (?,?,?,?)",
                             (s, float(precio), (nota or "")[:80], ahora))
        self._niv = None
        return cur.lastrowid

    def quitar_nivel(self, i):
        with self.lock, self._db() as db, db:
            db.execute("UPDATE niveles_usuario SET activo=0 WHERE id=?", (i,))
        self._niv = None

    # ---------- estado ----------
    def _guardar_estado(self, db, clave, valor, ahora):
        self.estado[clave] = valor
        db.execute("INSERT OR REPLACE INTO estado_avisos VALUES (?,?,?)", (clave, json.dumps(valor), ahora))

    def _registrar(self, db, clave, tipo, s, estado, t_dato, t_llegada, ahora, titulo, texto, detalle):
        t_em = max(ahora, self.reloj())
        cur = db.execute("""INSERT OR IGNORE INTO avisos (clave, tipo, simbolo, estado, t_dato, t_llegada, t_deteccion, t_emision,
                            titulo, texto, detalle) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (clave, tipo, s, estado, t_dato, t_llegada, ahora, t_em, titulo, texto,
                          json.dumps(detalle, ensure_ascii=False, allow_nan=False)))
        return cur.lastrowid if cur.rowcount else None

    # ---------- detección (cada segundo, desde el ciclo en vivo) ----------
    def observar(self, s, op, decision, fresco, ahora, contexto):
        """op: última operación del WebSocket {p, t, recibido, id} o None; decision: dict de la decisión spot;
        fresco: cotización vigente y flujo conectado; contexto: función que devuelve la foto del contexto actual."""
        if s not in ACTIVOS:
            return []
        nuevos = []
        with self.lock:
            db = _Perezosa(self.ruta)          # solo se abre la base si hay algo que escribir (esto corre cada segundo)
            try:
                if fresco and op and op.get("p"):
                    for nv in self.niveles(s):
                        a = self._cruce(db, s, nv, op, ahora, contexto)
                        if a:
                            nuevos.append(a)
                a = self._contexto(db, s, decision, fresco, ahora, contexto)
                if a:
                    nuevos.append(a)
                db.confirmar()
            finally:
                db.cerrar()
        return nuevos

    def _cruce(self, db, s, nv, op, ahora, contexto):
        k = f"nivel:{nv['id']}"
        st = dict(self.estado.get(k) or {})
        ld = lado(op["p"], nv["precio"])
        hueco = ahora - self.t_obs.get(k, 0) > CONTINUIDAD
        self.t_obs[k] = ahora
        if ld is None:
            return None
        previo = st.get("lado")
        if previo is None or hueco:
            if previo and ld != previo:
                self._registrar(db, f"omitido:{k}:{op['t']}:{ld}", "cruce", s, "omitido", op["t"], op.get("recibido"), ahora,
                                f"{s} · cruce de {eur(nv['precio'])} sin aviso",
                                "El precio cambió de lado durante un hueco de datos (caducidad, reconexión o reinicio): no se "
                                "sabe cuándo cruzó, así que no se avisa.",
                                {"nivel": nv, "de": previo, "a": ld, "operacion": op, "motivo": "cruce durante un hueco de datos"})
            st.update(lado=ld)
            self._guardar_estado(db, k, st, ahora)
            return None
        if ld == previo:
            return None
        st["lado"] = ld
        if ahora - st.get("ultimo_aviso", 0) < ENFRIA:
            self._guardar_estado(db, k, st, ahora)      # dentro del enfriamiento: se actualiza en silencio
            return None
        st["ultimo_aviso"] = ahora
        self._guardar_estado(db, k, st, ahora)
        sube = ld == "arriba"
        titulo = f"{s} cruza {eur(nv['precio'])} {'al alza' if sube else 'a la baja'}"
        texto = (f"Operación en Kraken a {eur(op['p'])}: el precio pasa {'por encima' if sube else 'por debajo'} de un nivel que "
                 f"sigues{(' (' + nv['nota'] + ')') if nv.get('nota') else ''}.")
        i = self._registrar(db, f"cruce:{nv['id']}:{op['t']}:{ld}", "cruce", s, "emitido", op["t"], op.get("recibido"), ahora,
                            titulo, texto, {"nivel": nv, "de": previo, "a": ld, "banda_pct": BANDA * 100, "operacion": op,
                                            "condicion": f"Última operación {'≥' if sube else '≤'} nivel {'+' if sube else '−'}0,1 %",
                                            "contexto": contexto()})
        return i and {"id": i, "titulo": titulo}

    def _contexto(self, db, s, d, fresco, ahora, contexto):
        k = f"contexto:{s}"
        v = (d or {}).get("accion")
        if not fresco or v not in TXT:
            self.cand.pop(s, None)            # dato caducado o análisis incompleto: la cuenta se interrumpe
            return None
        hueco = ahora - self.t_obs.get(k, 0) > CONTINUIDAD
        self.t_obs[k] = ahora
        if hueco:
            self.cand.pop(s, None)
        st = dict(self.estado.get(k) or {})
        actual = st.get("actual")
        if actual is None:
            self._guardar_estado(db, k, {"actual": v, "desde": ahora}, ahora)
            return None
        if v == actual:
            self.cand.pop(s, None)
            return None
        c = self.cand.get(s)
        if not c or c[0] != v:
            self.cand[s] = (v, ahora)
            return None
        if ahora - c[1] < PERIODO_CONTEXTO:
            return None
        self.cand.pop(s, None)
        self._guardar_estado(db, k, {"actual": v, "desde": c[1]}, ahora)
        titulo = f"{s} · decisión spot: {TXT[v].lower()}"
        texto = (f"Pasa de «{TXT[actual]}» a «{TXT[v]}» y se mantiene {PERIODO_CONTEXTO // 60000} min seguidos con datos "
                 f"vigentes. Motivo: {d.get('motivo', '—')}.")
        i = self._registrar(db, f"contexto:{s}:{c[1]}:{v}", "contexto", s, "emitido", d.get("precio_t"), d.get("precio_t"), ahora,
                            titulo, texto, {"de": actual, "a": v, "desde": c[1], "periodo_s": PERIODO_CONTEXTO // 1000,
                                            "condicion": f"Decisión spot distinta de «{TXT[actual]}» durante {PERIODO_CONTEXTO // 60000} min",
                                            "contexto": contexto()})
        return i and {"id": i, "titulo": titulo}

    # ---------- lectura y entrega ----------
    def lista(self, desde=0, limite=100, s=None):
        with self._db() as db:
            filas = db.execute("""SELECT id, tipo, simbolo, estado, t_dato, t_llegada, t_deteccion, t_emision, t_entrega, canal,
                                  titulo, texto, detalle FROM avisos WHERE id>? AND (? IS NULL OR simbolo=?) ORDER BY id DESC LIMIT ?""",
                               (desde, s, s, limite)).fetchall()
        k = ("id", "tipo", "simbolo", "estado", "t_dato", "t_llegada", "t_deteccion", "t_emision", "t_entrega", "canal",
             "titulo", "texto", "detalle")
        out = []
        for f in filas:
            x = dict(zip(k, f))
            x["detalle"] = json.loads(x["detalle"] or "{}")
            out.append(x)
        return out

    def ultimo_id(self):
        with self._db() as db:
            return db.execute("SELECT coalesce(max(id), 0) FROM avisos").fetchone()[0]

    def entregar(self, i, canal, ahora):
        """Registra la entrega (una sola vez) cuando la página confirma que mostró el aviso."""
        if canal not in ("pantalla", "sistema"):
            raise ValueError("canal no válido")
        with self.lock, self._db() as db, db:
            cur = db.execute("UPDATE avisos SET t_entrega=?, canal=? WHERE id=? AND t_entrega IS NULL AND estado='emitido'",
                             (ahora, canal, i))
        return cur.rowcount == 1
