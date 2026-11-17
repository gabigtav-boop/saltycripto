"""Saltycripto · servidor local. Arranca con:  .venv/bin/python servidor.py
Abre el navegador en http://localhost:8502 (solo análisis, no opera)."""
import argparse
import gzip
import json
import mimetypes
import pathlib
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from cripto.servicio import COINS, Servicio

WEB = pathlib.Path(__file__).with_name("web")
SV = Servicio()
TFS_OK = {"1m", "1h", "4h", "1d", "1w"}


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
            while True:     # un envío por segundo (también sirve de latido para el navegador)
                self.wfile.write(b"data: " + SV.tick_payload(sel, tf, memo).encode() + b"\n\n")
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
        if u.path == "/api/health":
            return self._send(200, json.dumps(SV.salud()).encode())
        if u.path.startswith("/vendor/") or u.path in ("/favicon.svg",):
            return self._static(u.path.lstrip("/"))
        self._send(404, b"{}")

    def do_POST(self):
        # solo peticiones de la propia app: JSON y mismo origen (otra web no puede tocar el motor)
        org = self.headers.get("Origin")
        if not self._host_ok() or not (self.headers.get("Content-Type") or "").startswith("application/json") or (
                org and urlparse(org).hostname not in ("localhost", "127.0.0.1")):
            return self._send(403, b"{}")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            b = {}
        p = urlparse(self.path).path
        if p == "/api/refresh":
            threading.Thread(target=SV.main, args=(False,), daemon=True).start()
        elif p == "/api/retry" and b.get("s") in COINS:
            threading.Thread(target=SV.load_coin, args=(b["s"],), daemon=True).start()
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
    srv = Servidor(("127.0.0.1", a.port), H)
    SV.start()
    url = f"http://localhost:{a.port}"
    print(f"Saltycripto en {url}  ·  Ctrl+C para salir", flush=True)
    if not a.no_open:
        threading.Timer(.8, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
