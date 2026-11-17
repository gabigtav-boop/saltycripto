"""Pruebas de robustez del motor: recargas, fuentes que fallan e histórico profundo."""
from cripto import fuentes
from cripto.historico import Historico
from cripto.servicio import Servicio, heredar


def test_recarga_conserva_la_flecha():
    """Al recargar una moneda (cada 10 min) se conservan calibración, proyección y lectura en vivo."""
    viejo = {"calib": {"4h": 1}, "proy": {"4h": 2}, "prev": {"dir": "lateral"}, "vivo": {"ts": 40}, "micro": {}, "tkv": {}, "otra": 9}
    nuevo = heredar({"price": 100}, viejo)
    assert nuevo["calib"] == {"4h": 1} and nuevo["proy"] == {"4h": 2} and nuevo["prev"]["dir"] == "lateral"
    assert nuevo["price"] == 100 and "otra" not in nuevo


def test_contexto_no_pierde_datos_si_una_fuente_falla(monkeypatch):
    sv = Servicio(net=False)
    sv.ctx = {"fg": {"value": "20"}, "dom": 58.0, "mc": -1.0}

    def jget(url, timeout=12):
        if "alternative.me" in url:
            return {"data": [{"value": "25"}, {"value": "24"}]}
        raise RuntimeError("429")          # CoinGecko limita las peticiones
    monkeypatch.setattr(fuentes, "jget", jget)
    sv.load_ctx()
    assert sv.ctx["fg"]["value"] == "25" and sv.ctx["dom"] == 58.0 and sv.ctx["mc"] == -1.0


def test_historico_no_se_vuelve_a_descargar_si_el_mercado_no_tiene_mas(tmp_path):
    """Monedas nuevas (HYPE) tienen poca historia: tras la descarga inicial solo se piden velas nuevas."""
    h = Historico(directorio=tmp_path, net=True)
    H = 3600000
    ahora = 5000 * H
    llamadas = []

    def descargar(sym, tf, n, desde):
        llamadas.append(desde)
        ini = 0 if desde is None else desde
        return "binance:X", [{"t": t, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 1} for t in range(max(ini, 4000 * H), 5000 * H, H)][-1000 if desde else -n:]
    h._descargar = descargar
    r1 = h.actualizar("HYPE", "1h", ahora_ms=ahora)
    assert len(r1) < 3600 and llamadas == [None]
    h.actualizar("HYPE", "1h", ahora_ms=ahora + 3 * H)            # pasan 3 h: solo velas nuevas
    assert llamadas[-1] is not None and len(llamadas) == 2
    assert h.cargar("HYPE", "1h")["rows"][-1]["t"] <= ahora + 3 * H


def test_historico_se_guarda_y_se_recarga(tmp_path):
    h = Historico(directorio=tmp_path, net=True)
    H = 3600000
    h._descargar = lambda sym, tf, n, desde: ("binance:X", [{"t": t * H, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 1} for t in range(0, 4000)])
    h.actualizar("BTC", "1h", ahora_ms=4001 * H)
    h2 = Historico(directorio=tmp_path, net=False)
    assert len(h2.cargar("BTC", "1h")["rows"]) >= 3990 and h2.estado[("BTC", "1h")]["n"] >= 3990


def test_servidor_rechaza_peticiones_que_no_llegan_por_localhost():
    """Una web externa cuyo dominio apunte a 127.0.0.1 («rebinding» de DNS) enviaría su nombre en Host: se rechaza."""
    import http.client
    import threading

    import servidor

    srv = servidor.Servidor(("127.0.0.1", 0), servidor.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def pide(host, ruta="/api/health", metodo="GET"):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request(metodo, ruta, body=b"{}" if metodo == "POST" else None,
                      headers={"Host": host, **({"Content-Type": "application/json"} if metodo == "POST" else {})})
            r = c.getresponse()
            r.read()
            c.close()
            return r.status
        assert pide("localhost:8501") == 200 and pide("127.0.0.1:8501") == 200
        assert pide("malo.example.com") == 403 and pide("malo.example.com:8501") == 403
        assert pide("malo.example.com", "/api/refresh", "POST") == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_velas_defectuosas_se_descartan_antes_de_analizar():
    from cripto.fuentes import parse_coinbase, parse_ohlc, sanear
    base = {"o": 10.0, "h": 11.0, "l": 9.0, "c": 10.5, "v": 5.0}
    filas = [dict(base, t=3000), dict(base, t=1000), dict(base, t=2000, c=0.0), dict(base, t=4000, o=float("nan")),
             dict(base, t=5000, h=9.5), dict(base, t=3000, v=7.0), dict(base, t=6000, v=-1.0)]
    r = sanear(filas)
    assert [x["t"] for x in r] == [1000, 3000, 5000, 6000]                 # ordenadas, sin ceros ni NaN, sin tiempos repetidos
    assert r[1]["v"] == 7.0                                                  # de dos velas con el mismo tiempo manda la última
    assert r[2]["h"] >= max(r[2]["o"], r[2]["c"]) and r[3]["v"] == 0.0     # máximo coherente; volumen negativo → 0
    kr = {"XBTEUR": [[1, "10", "11", "9", "10.5", "10", "3", 5], [2, "0", "1", "0", "0", "0", "3", 5]], "last": 2}
    assert [x["t"] for x in parse_ohlc(kr)] == [1000]
    cb = [[2, 9, 11, 10, 10.5, 4], [1, 9, 11, 10, -1, 4]]
    assert [x["t"] for x in parse_coinbase(cb)] == [2000]
