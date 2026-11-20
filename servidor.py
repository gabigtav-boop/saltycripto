"""Saltycripto · servidor local. Arranca con:  .venv/bin/python servidor.py
Abre el navegador en http://localhost:8502 (solo análisis, no opera)."""
import argparse
import gzip
import json
import mimetypes
import pathlib
import sqlite3
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cripto import catalogo
from cripto import eventos as ev_clave
from cripto.evidencias import ACTIVOS as EV_ACTIVOS
from cripto.servicio import COINS, Servicio, now_ms
from estudios import evaluar_spot

WEB = pathlib.Path(__file__).with_name("web")
SV = Servicio()
TFS_OK = {"1m", "1h", "4h", "1d", "1w"}
CATALOGO_JS = ("window.CATALOGO=" + json.dumps(catalogo.web(), ensure_ascii=False, separators=(",", ":")) + ";").encode()
_SPOT = {"t": 0, "body": None}       # el informe spot recorre todas las decisiones: se reutiliza durante 60 s


_GZ = {}


def _gzip(body):
    """Compresión gzip con caché (el estado completo solo cambia cuando cambia su versión)."""
    k = hash(body)
    if k not in _GZ:
        if len(_GZ) > 32:
            _GZ.clear()
        _GZ[k] = gzip.compress(body, 6)
    return _GZ[k]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silencioso
        pass

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        gz = False
        if len(body) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "") and (
                "json" in ctype or "text" in ctype or "javascript" in ctype or "svg" in ctype):
            body = _gzip(body)
            gz = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _static(self, rel):
        p = (WEB / rel).resolve()
        if WEB.resolve() not in p.parents or not p.is_file():
            return self._send(404, b"{}")
        ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ct.startswith("text/") or ct.endswith("javascript"):
            ct += "; charset=utf-8"
        self._send(200, p.read_bytes(), ct)

    def _stream(self, q):
        sel = q.get("sel", [None])[0]
        sel = sel if sel in COINS else None
        tf = q.get("tf", [None])[0]
        memo = {}
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            fallos = 0
            while True:     # un envío por segundo (también sirve de latido para el navegador)
                try:
                    cuerpo = SV.tick_payload(sel, tf, memo).encode()
                    fallos = 0
                except Exception as e:  # noqa: BLE001
                    # un error puntual al armar el envío (p. ej. un dato que cambia a mitad) no debe cortar el canal en vivo
                    fallos += 1
                    if fallos in (1, 30):
                        print(f"canal en vivo: {e!r}", flush=True)
                    if fallos >= 60:
                        return
                    self.wfile.write(b": latido\n\n")
                    self.wfile.flush()
                    time.sleep(1)
                    continue
                self.wfile.write(b"data: " + cuerpo + b"\n\n")
                self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _host_ok(self):
        """Solo se atiende a quien llega por localhost: una web externa que apunte su dominio a 127.0.0.1 («rebinding» de DNS)
        envía su propio nombre en Host y se rechaza."""
        h = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return h in ("localhost", "127.0.0.1")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"{}")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._static("index.html")
        if u.path == "/clasica":
            return self._static("clasica.html")
        if u.path == "/catalogo.js":           # la lista de monedas de la página sale del catálogo del servidor
            return self._send(200, CATALOGO_JS, "text/javascript; charset=utf-8")
        if u.path == "/api/state":
            v = q.get("v", [None])[0]
            try:
                since = int(v) if v is not None else None
            except ValueError:
                since = None
            body = SV.snapshot(since)
            return self._send(204) if body is None else self._send(200, body)
        if u.path == "/api/stream":
            return self._stream(q)
        if u.path == "/api/velas":
            s, tf = q.get("s", [""])[0], q.get("tf", [""])[0]
            if s not in COINS or tf not in TFS_OK:
                return self._send(400, b"{}")
            return self._send(200, SV.velas(s, tf).encode())
        if u.path == "/api/eventos":
            return self._json({"lista": SV.ev_pub, "candidatos": SV.ev_cand, "error": SV.eventos.error,
                               "tipos": ev_clave.TIPOS, "certezas": ev_clave.CERTEZAS})
        if u.path == "/api/coste":
            s, lado, tipo = q.get("s", [""])[0], q.get("lado", ["compra"])[0], q.get("tipo", ["mercado"])[0]
            try:
                mk, tk = float(q.get("maker", [".004"])[0]), float(q.get("taker", [".008"])[0])
                lim = float(q["limite"][0]) if q.get("limite") else None
            except ValueError:
                return self._send(400, b"{}")
            if s not in COINS or lado not in ("compra", "venta") or tipo not in ("mercado", "limite") or not (0 <= mk < .1 and 0 <= tk < .1):
                return self._send(400, b"{}")
            return self._send(200, json.dumps(SV.coste(s, lado, mk, tk, tipo, lim), ensure_ascii=False,
                                              default=lambda x: None).replace("Infinity", "null").encode())
        if u.path == "/api/health":
            return self._send(200, json.dumps(SV.salud()).encode())
        if u.path.startswith("/api/evid") or u.path in ("/api/minutos", "/api/avisos", "/api/niveles", "/api/spot", "/api/historial"):
            return self._evidencias(u.path, q)
        if u.path.startswith(("/vendor/", "/lib/")) or u.path in ("/favicon.svg",):
            return self._static(u.path.lstrip("/"))
        self._send(404, b"{}")

    def _json(self, x):
        return self._send(200, json.dumps(x, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())

    def _evidencias(self, ruta, q):
        """Evidencias, minutos, avisos, niveles e historial de las monedas del catálogo (lectura, para el diagnóstico)."""
        s = q.get("s", ["BTC"])[0]
        if s not in EV_ACTIVOS and not (ruta == "/api/evidencias" and s == "*") and ruta not in ("/api/avisos", "/api/spot", "/api/historial"):
            return self._send(400, b"{}")
        if SV.evid is None or SV.avisos is None:
            return self._send(503, json.dumps({"error": SV.ev_error or "evidencias no disponibles"}).encode())
        try:
            ahora = now_ms()
            if ruta == "/api/evidencias":
                return self._json(SV.evid.resumen(s, ahora))
            if ruta == "/api/evidencia":
                d = SV.evid.detalle(s, int(q.get("h", ["0"])[0]))
                return self._json(d) if d else self._send(404, b"{}")
            if ruta == "/api/minutos":
                return self._json({"s": s, "minutos": SV.evid.minutos(s, max(10, min(720, int(q.get("n", ["120"])[0]))), ahora)})
            if ruta == "/api/avisos":
                f = q.get("s", [None])[0]
                return self._json({"avisos": SV.avisos.lista(int(q.get("desde", ["0"])[0]), 200, f if f in EV_ACTIVOS else None),
                                   "niveles": SV.avisos.niveles(), "ultimo": SV.av_ultimo})
            if ruta == "/api/niveles":
                return self._json({"niveles": SV.avisos.niveles(s)})
            if ruta == "/api/historial":
                return self._json(SV.hist_ev or SV.evid.historial(ahora))
            if ruta == "/api/spot":
                if ahora - _SPOT["t"] > 60000 or _SPOT["body"] is None:
                    with sqlite3.connect(SV.evid.ruta.as_uri() + "?mode=ro", uri=True) as db:
                        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='decisiones'").fetchone():
                            return self._json({"total": 0, "evaluadas": 0, "excluidas": {}, "activos": {}})
                        _SPOT["body"] = evaluar_spot.informe(db, ahora, .001)
                    _SPOT["t"] = ahora
                # el evaluador nuevo (C/E/V/ND) se calcula en segundo plano: aquí solo se lee el último resumen
                return self._json(dict(_SPOT["body"], v2=SV.spot_inf, v2_error=SV.spot_error))
        except (ValueError, sqlite3.Error) as e:
            return self._send(500, json.dumps({"error": str(e)[:200]}).encode())
        return self._send(404, b"{}")

    def do_POST(self):
        # solo peticiones de la propia app: JSON y mismo origen (otra web no puede tocar el motor)
        org = self.headers.get("Origin")
        if not self._host_ok() or not (self.headers.get("Content-Type") or "").startswith("application/json") or (
                org and urlparse(org).hostname not in ("localhost", "127.0.0.1")):
            return self._send(403, b"{}")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}") if 0 <= n <= 65536 else {}
        except ValueError:
            b = {}
        p = urlparse(self.path).path
        if p == "/api/refresh":
            threading.Thread(target=SV.main, args=(True,), daemon=True).start()   # sin vaciar: mientras recarga se siguen viendo los datos
        elif p == "/api/retry" and b.get("s") in COINS:
            threading.Thread(target=SV.load_coin, args=(b["s"],), daemon=True).start()
        elif p == "/api/avisos/entrega" and SV.avisos and isinstance(b.get("id"), int):
            ok = SV.avisos.entregar(b["id"], b.get("canal"), now_ms())
            return self._send(200, json.dumps({"registrada": ok}).encode())
        elif p == "/api/niveles" and SV.avisos:
            try:
                if b.get("accion") == "anadir" and b.get("s") in EV_ACTIVOS:
                    i = SV.avisos.anadir_nivel(b["s"], float(b.get("precio")), str(b.get("nota") or ""), now_ms())
                    return self._send(200, json.dumps({"id": i}).encode())
                if b.get("accion") == "quitar" and isinstance(b.get("id"), int):
                    SV.avisos.quitar_nivel(b["id"])
                else:
                    return self._send(400, b"{}")
            except (TypeError, ValueError):
                return self._send(400, b"{}")
        elif p == "/api/eventos":
            try:
                if b.get("accion") == "anadir":
                    i = SV.eventos.anadir(dict(b.get("evento") or {}, creado=now_ms()))
                elif b.get("accion") == "quitar" and isinstance(b.get("id"), str):
                    i = SV.eventos.quitar(b["id"])
                elif b.get("accion") == "descartar" and isinstance(b.get("clave"), str):
                    i = SV.eventos.descartar(b["clave"])
                else:
                    return self._send(400, b"{}")
            except (TypeError, ValueError, OSError) as e:
                return self._send(400, json.dumps({"error": str(e)[:160]}, ensure_ascii=False).encode())
            SV.recalc_eventos()
            return self._send(200, json.dumps({"ok": True, "id": i}).encode())
        elif p == "/api/prefs" and isinstance(b.get("off"), list):
            SV.off = {s for s in b["off"] if s in COINS}
            if SV.last:
                threading.Thread(target=SV.load_missing, daemon=True).start()
        else:
            return self._send(404, b"{}")
        self._send(200, b"{}")


class Servidor(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """El navegador corta conexiones (recarga, cambio de pestaña): no es un error del motor."""
        import sys
        t = sys.exc_info()[0]
        if t is None or issubclass(t, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8502)
    ap.add_argument("--no-open", action="store_true", help="no abrir el navegador")
    a = ap.parse_args()
    from cripto.version import RAIZ, VERSION
    try:
        srv = Servidor(("127.0.0.1", a.port), H)
    except OSError as e:
        # el lanzador lee esta línea para distinguir «puerto ocupado» de otros fallos
        print(f"ERROR_PUERTO_OCUPADO {a.port}: {e}", flush=True)
        raise SystemExit(3)
    SV.puerto = a.port
    SV.start()
    url = f"http://localhost:{a.port}"
    print(f"Saltycripto {VERSION} en {url} · carpeta {RAIZ} · Ctrl+C para salir", flush=True)
    if not a.no_open:
        threading.Timer(.8, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
