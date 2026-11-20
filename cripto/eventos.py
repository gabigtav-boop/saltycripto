"""Eventos clave: lo que mueve el mercado y no está en el calendario macro (salidas a bolsa, decisiones sobre ETF, leyes,
actualizaciones de red, desbloqueos de tokens…).

· La lista es curada: `datos/eventos.json`, editable a mano o desde la página (Noticias › Eventos clave). Las noticias solo
  PROPONEN candidatos (`candidatos`); nunca se añade un evento solo, porque un rumor y un hecho no deben mezclarse.
· Cada evento lleva su certeza (confirmado, probable, rumor), su fecha exacta o aproximada y el canal por el que puede
  afectar a las criptomonedas.
· Tras el evento se mide la reacción del precio (24 h y 7 días) y se compara con el movimiento habitual del activo en ese
  plazo. Es descriptivo: no demuestra causa ni entra en la previsión (sus pesos no están validados)."""
import json
import math
import pathlib
import re
import threading
import time
import unicodedata
from urllib.parse import urlparse

from .catalogo import SIMBOLOS
from .hora import a_canarias

RUTA = pathlib.Path(__file__).resolve().parents[1] / "datos" / "eventos.json"
TIPOS = {"salida_bolsa": "Salida a bolsa", "regulacion": "Regulación", "etf": "ETF", "red": "Actualización de red",
         "desbloqueo": "Desbloqueo de tokens", "macro": "Macro", "empresa": "Empresa", "otro": "Otro"}
CERTEZAS = ("confirmado", "probable", "rumor")
IMPACTOS = ("alto", "medio")
ACTIVOS_OK = set(SIMBOLOS) | {"MERCADO"}
DIA = 86_400_000
HORA = 3_600_000
RECIENTE = 14 * DIA          # un evento pasado se sigue mostrando (con su reacción) durante 14 días
_lock = threading.RLock()          # reentrante: anadir/quitar llaman a recargar con el cerrojo tomado


def _txt(x, n):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def validar(e):
    """Evento limpio o ValueError. `fecha` en ms (UTC) o None; `fecha_txt` para fechas aproximadas («2027»)."""
    if not isinstance(e, dict):
        raise ValueError("evento no válido")
    titulo = _txt(e.get("titulo"), 120)
    if len(titulo) < 3:
        raise ValueError("falta el título")
    tipo = e.get("tipo") if e.get("tipo") in TIPOS else "otro"
    certeza = e.get("certeza") if e.get("certeza") in CERTEZAS else "rumor"
    impacto = e.get("impacto") if e.get("impacto") in IMPACTOS else "medio"
    f = e.get("fecha")
    if f is not None:
        if isinstance(f, bool) or not isinstance(f, (int, float)) or not math.isfinite(f) or not 946_684_800_000 <= f <= 4_102_444_800_000:
            raise ValueError("fecha fuera de rango")
        f = int(f)
    activos = [a for a in (e.get("activos") or ["MERCADO"]) if a in ACTIVOS_OK][:9] or ["MERCADO"]
    fuente = _txt(e.get("fuente"), 300)
    if fuente and urlparse(fuente).scheme not in ("http", "https"):
        fuente = ""
    return {"id": _txt(e.get("id"), 40) or _id(titulo, f), "titulo": titulo, "tipo": tipo, "certeza": certeza,
            "impacto": impacto, "fecha": f, "fecha_txt": _txt(e.get("fecha_txt"), 120), "hora_exacta": bool(e.get("hora_exacta")),
            "activos": activos, "canal": _txt(e.get("canal"), 240), "datos": _txt(e.get("datos"), 300),
            "fuente": fuente, "origen": e.get("origen") if e.get("origen") in ("manual", "noticia") else "manual",
            "creado": int(e.get("creado") or time.time() * 1000)}


def _id(titulo, f):
    base = unicodedata.normalize("NFKD", titulo.lower()).encode("ascii", "ignore").decode()
    return (re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:28] or "evento") + "-" + format(int(f or time.time() * 1000) % 1_000_000, "x")


class Eventos:
    """Lista curada en disco. Se relee si el archivo cambia (edición a mano) y se escribe de forma atómica."""

    def __init__(self, ruta=RUTA):
        self.ruta = pathlib.Path(ruta) if ruta else None       # None: solo en memoria (pruebas)
        self.items, self.descartados, self.error, self._mtime = [], [], None, None
        self.recargar()

    def recargar(self):
        with _lock:                     # el hilo de cada minuto relee mientras la página puede estar añadiendo un evento
            return self._recargar()

    def _recargar(self):
        if self.ruta is None:
            return False
        try:
            m = self.ruta.stat().st_mtime_ns
        except OSError:
            m = None
        if m == self._mtime:
            return False
        self._mtime = m
        if m is None:
            self.items, self.descartados, self.error = [], [], None
            return True
        try:
            d = json.loads(self.ruta.read_text())
            items, malos = [], 0
            for e in d.get("eventos", []):
                try:
                    items.append(validar(e))
                except ValueError:
                    malos += 1
            self.items = items
            self.descartados = [str(x)[:60] for x in d.get("descartados", [])][-300:]
            self.error = f"{malos} eventos no válidos en eventos.json (se ignoran)" if malos else None
        except (OSError, ValueError, AttributeError) as e:
            self.error = f"no se pudo leer eventos.json: {e}"[:160]
        return True

    def _guardar(self):
        if self.ruta is None:
            return
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps({"eventos": self.items, "descartados": self.descartados}, ensure_ascii=False, indent=1))
        tmp.replace(self.ruta)
        self._mtime = self.ruta.stat().st_mtime_ns

    def anadir(self, e):
        x = validar(e)
        with _lock:
            self.recargar()
            if any(y["id"] == x["id"] for y in self.items):
                raise ValueError("ya existe un evento con ese identificador")
            self.items.append(x)
            self._guardar()
        return x["id"]

    def quitar(self, i):
        with _lock:
            self.recargar()
            n = len(self.items)
            self.items = [y for y in self.items if y["id"] != i]
            if len(self.items) != n:
                self._guardar()
                return True
        return False

    def descartar(self, clave):
        with _lock:
            self.recargar()
            if clave not in self.descartados:
                self.descartados = (self.descartados + [str(clave)[:60]])[-300:]
                self._guardar()


def estado(e, ahora):
    """hoy / previsto / pasado / sin_fecha. «Hoy» es el día de Canarias."""
    f = e.get("fecha")
    if f is None:
        return "sin_fecha"
    if a_canarias(f / 1000).date() == a_canarias(ahora / 1000).date():
        return "hoy"
    return "previsto" if f > ahora else "pasado"


def _precio_en(rows, t):
    """Apertura de la vela de 1 h que contiene t (no anticipa el cierre)."""
    lo, hi = 0, len(rows) - 1
    if not rows or t < rows[0]["t"] or t >= rows[-1]["t"] + HORA:
        return None
    while lo < hi:
        m = (lo + hi + 1) // 2
        if rows[m]["t"] <= t:
            lo = m
        else:
            hi = m - 1
    r = rows[lo]
    return r["o"] if t - r["t"] < HORA else None


def _sd_plazo(rows, t, horas, dias=90):
    """Desviación típica de los rendimientos logarítmicos a `horas` en los `dias` previos a t (sin solapar ventanas)."""
    ant = [r for r in rows if t - dias * DIA <= r["t"] < t]
    v = [math.log(ant[i + horas]["c"] / ant[i]["c"]) for i in range(0, len(ant) - horas, horas)
         if ant[i]["c"] > 0 and ant[i + horas]["c"] > 0]
    if len(v) < 8:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def reaccion(e, series, ahora, vivos=None):
    """Movimiento de cada activo afectado desde el evento: lo que lleva (`desde`, con el precio en vivo si lo hay) y, al
    cumplirse cada plazo, el de 24 h y 7 días frente a su movimiento habitual en ese plazo (desviación de los 90 días
    previos). `series`: {símbolo: velas de 1 h}; MERCADO se mide con BTC. Descriptivo: no demuestra causa."""
    f = e.get("fecha")
    if f is None or f > ahora:
        return None
    out = {}
    for a in e["activos"]:
        s = "BTC" if a == "MERCADO" else a
        rows = series.get(s) or []
        p0 = _precio_en(rows, f)
        if not p0:
            continue
        r = {"activo": s, "p0": p0}
        pv = (vivos or {}).get(s) or (rows[-1]["c"] if rows else None)
        if pv and ahora - f < 168 * HORA:
            r["desde"] = {"pct": (pv / p0 - 1) * 100, "horas": round((ahora - f) / HORA, 1)}
        for k, h in (("24h", 24), ("7d", 168)):
            fin = f + h * HORA
            p = _precio_en(rows, fin) if ahora >= fin + HORA else None
            if not p:
                continue
            sd = _sd_plazo(rows, f, h)
            z = math.log(p / p0) / sd if sd else None
            r[k] = {"pct": (p / p0 - 1) * 100, "z": z,
                    "lectura": None if z is None else "dentro de lo habitual" if abs(z) < 1 else
                    "movimiento amplio" if abs(z) < 2 else "movimiento inusual"}
        out[s] = r
    return out or None


def publico(items, ahora, series=None, vivos=None):
    """Lo que ve la página: eventos de hoy, próximos, recientes (14 días) y sin fecha, con su estado y la reacción."""
    out = []
    for e in items:
        st = estado(e, ahora)
        if st == "pasado" and ahora - e["fecha"] > RECIENTE:
            continue
        x = dict(e, estado=st)
        if series is not None and st in ("hoy", "pasado"):
            x["reaccion"] = reaccion(e, series, ahora, vivos)
        out.append(x)
    orden = {"hoy": 0, "previsto": 1, "sin_fecha": 2, "pasado": 3}
    out.sort(key=lambda x: (orden[x["estado"]], x["fecha"] if x["estado"] != "pasado" else -(x["fecha"] or 0)) if x["fecha"]
             else (orden[x["estado"]], 0))
    return out


# ---------- candidatos desde las noticias ----------
_PAL = re.compile(r"[a-z0-9áéíóúñ]{4,}")
_VACIAS = {"with", "from", "that", "this", "will", "what", "after", "their", "como", "para", "sobre", "tras", "desde",
           "stock", "shares", "market", "markets", "crypto", "bitcoin", "today", "first", "says", "said"}


def _claves(t):
    return {w for w in _PAL.findall(t.lower()) if w not in _VACIAS}


_GENERICOS = {"salida", "bolsa", "nasdaq", "nyse", "sale", "salen", "ley", "etf", "red", "the", "de", "la", "el"}


def _propios(t):
    """Nombres propios de un título («Anthropic», «OpenAI»): bastan para saber de qué empresa o proyecto habla."""
    return {w.lower() for w in re.findall(r"\b[A-Z][A-Za-z0-9]{3,}\b", t) if w.lower() not in _GENERICOS}


def candidatos(noticias, items, descartados, ahora, n=6):
    """Noticias de las últimas 72 h que parecen un evento clave (salida a bolsa, o regulación/ETF con fuerza alta en
    varias fuentes) y que no están ya en la lista ni descartadas. Solo se proponen: el usuario decide."""
    ya = [_claves(e["titulo"]) for e in items]
    propios = {}
    for e in items:
        for w in _propios(e["titulo"]):
            propios.setdefault(w, []).append(e)
    out, vistos = [], []
    for x in noticias:
        if ahora - x.get("t", 0) > 3 * DIA:
            continue
        temas = x.get("temas") or []
        ipo = "Salidas a bolsa" in temas
        cob = x.get("cobertura") or 1
        fuerte = (x.get("fuerza") or 0) >= 6 and cob >= 2 and any(t in temas for t in ("Regulación", "ETF e institucionales"))
        if not ((ipo and (cob >= 2 or (x.get("fuerza") or 0) >= 1.5)) or fuerte):
            continue
        k = _claves(x.get("titulo") or "")
        clave = re.sub(r"\W+", "", (x.get("titulo") or "").lower())[:60]
        tipo = "salida_bolsa" if ipo else ("etf" if "ETF e institucionales" in temas else "regulacion")
        # misma empresa y mismo tipo que un evento guardado: si ese evento ya tiene fecha, la noticia no aporta un evento
        # nuevo; si no la tiene, se propone como posible actualización (p. ej. OpenAI anuncia el día de su salida a bolsa)
        mismos = [e for w in k for e in propios.get(w, []) if e["tipo"] == tipo]
        actualiza = next((e for e in mismos if e.get("fecha") is None), None)
        if clave in descartados or (mismos and not actualiza) or (not actualiza and any(len(k & y) >= 2 for y in ya)) \
                or any(len(k & y) >= 3 for y in vistos):
            continue
        vistos.append(k)
        out.append({"clave": clave, "titulo": x.get("titulo"), "t": x.get("t"), "cobertura": cob,
                    "fuente": x.get("fuente"), "url": x.get("link"), "tipo": tipo,
                    "actualiza": actualiza and {"id": actualiza["id"], "titulo": actualiza["titulo"]},
                    "activos": [a for a in (x.get("afecta") or {}) if a in ACTIVOS_OK] or ["MERCADO"]})
        if len(out) >= n:
            break
    return out
