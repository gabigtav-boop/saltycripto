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
