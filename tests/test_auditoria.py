"""Pruebas de la auditoría (16 nov 2026): una sola carga a la vez, recargas que no pierden datos, fuentes que caducan,
histórico con cerrojo y escritura atómica, y proyección robusta frente a velas defectuosas."""
import threading
import time

import numpy as np

from cripto import servicio as sv_mod
from cripto.historico import Historico
from cripto.proyeccion import calibrar, limpiar, volatilidad
from cripto.servicio import Servicio


def _servicio_sin_red(monkeypatch):
    """Servicio con todas las fuentes externas sustituidas por funciones vacías: solo se prueba la lógica de la carga."""
    s = Servicio(net=False)
    for nombre in ("find_pairs", "tick_all", "start_stream", "load_ctx", "cb_candles", "refresh_src", "sembrar_1m", "add_bt",
                   "calibrar_moneda", "do_validate"):
        monkeypatch.setattr(s, nombre, lambda *a, **k: None)
    monkeypatch.setattr(s, "cg_markets", lambda *a, **k: None)
    monkeypatch.setattr(s, "cb_spot", lambda *a, **k: None)
    s.pairs = {"BTC": "XBTEUR"}
    s.order = ["BTC"]
    return s


# ---------- carga completa ----------
def test_solo_una_carga_completa_a_la_vez(monkeypatch):
    s = _servicio_sin_red(monkeypatch)
    entradas, soltar = [], threading.Event()

    def lenta(silent=False):
        entradas.append(1)
        soltar.wait(5)
    monkeypatch.setattr(s, "_main", lenta)
    a = threading.Thread(target=s.main, args=(True,))
    a.start()
    time.sleep(.2)
    s.main(True)                       # la segunda llamada, con la primera en marcha, no hace nada (antes entraba a los 300 s)
    soltar.set()
    a.join()
    assert len(entradas) == 1
    s.main(True)                       # y al terminar la primera, se puede volver a cargar
    assert len(entradas) == 2


def test_una_recarga_con_fallo_puntual_no_pierde_la_moneda(monkeypatch):
    s = _servicio_sin_red(monkeypatch)
    s.data = {"BTC": {"sym": "BTC", "price": 50000.0, "res": {"1d": 1}}}
    monkeypatch.setattr(s, "run", lambda sym, now=None: {"sym": sym, "fail": "precio fuera de rango respecto a las velas (dato sospechoso)"})
    s.main(True)                       # recarga silenciosa (la de cada 10 minutos y la del botón de recargar)
    assert s.data["BTC"]["price"] == 50000.0 and "sospechoso" in s.data["BTC"]["recarga_fallo"]
    # en la primera carga, sin datos previos, el fallo sí se muestra
    s2 = _servicio_sin_red(monkeypatch)
    monkeypatch.setattr(s2, "run", lambda sym, now=None: {"sym": sym, "fail": "sin velas"})
    s2.main(False)
    assert s2.data["BTC"]["fail"] == "sin velas"


# ---------- fuentes que caducan ----------
def test_precios_externos_caducados_no_se_usan_para_contrastar():
    s = Servicio(net=False)
    s.cbp, s.cbp_t = {"BTC": 100.0}, {"BTC": time.time() - 3600}
    assert s.p_coinbase("BTC") is None                          # hace una hora: la fuente puede estar caída
    s.cbp_t["BTC"] = time.time() - 60
    assert s.p_coinbase("BTC") == 100.0
    s.cbp, s.cbp_t = {"ETH": 5.0}, {}                           # sin marca de edad (datos cargados a mano): se usa tal cual
    assert s.p_coinbase("ETH") == 5.0
    s.mk, s.mkt = {"BTC": {"p": 7.0}}, time.time() - 7200
    assert s.p_coingecko("BTC") is None
    s.mkt = time.time() - 60
    assert s.p_coingecko("BTC") == 7.0


def test_derivados_caducados_no_entran_en_la_prevision():
    s = Servicio(net=False)
    s.deriv = {"BTC": {"ok": True, "funding": 1e-4}}
    s.deriv_t = sv_mod.now_ms() - 3600 * 1000                   # hace una hora
    assert s.deriv_fresco("BTC") is None
    s.deriv_t = sv_mod.now_ms() - 60 * 1000
    assert s.deriv_fresco("BTC")["funding"] == 1e-4


def test_precio_de_kraken_de_la_carga_se_renueva_si_es_viejo():
    class K:
        llamadas = []

        def get(self, path):
            K.llamadas.append(path)
            return {"XBTEUR": {"c": ["123.0", "1"]}}
    s = Servicio(kraken=K(), net=False)
    s.tk, s.tk_t = {"XBTEUR": {"c": ["100.0", "1"]}}, time.time()
    assert s.tick("XBTEUR") == 100.0 and not K.llamadas        # reciente: se usa la foto
    s.tk_t = time.time() - 120
    assert s.tick("XBTEUR") == 123.0 and K.llamadas            # vieja: se pide de nuevo ese par


def test_la_salud_no_comparte_diccionarios_con_otros_hilos():
    s = Servicio(net=False)
    s.fund.estado = {"CoinDesk": {"ok": True, "n": 3}}
    h = s.salud()
    s.fund.estado["Nueva"] = {"ok": False}                      # otro hilo añade una fuente mientras se serializa
    assert "Nueva" not in h["noticias"]


# ---------- histórico profundo ----------
def test_historico_descarga_una_vez_aunque_lo_pidan_varios_hilos(tmp_path):
    h = Historico(directorio=tmp_path, net=True)
    H = 3600000
    descargas = []

    def descargar(sym, tf, n, desde):
        descargas.append(desde)
        time.sleep(.2)
        return "binance:X", [{"t": t, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 1} for t in range(4000 * H, 5000 * H, H)]
    h._descargar = descargar
    ahora = 5000 * H + 10
    hilos = [threading.Thread(target=h.actualizar, args=("BTC", "1h", ahora)) for _ in range(6)]
    for t in hilos:
        t.start()
    for t in hilos:
        t.join()
    assert len(descargas) == 1                                  # antes cada hilo descargaba y escribía el mismo archivo


def test_historico_escribe_de_forma_atomica_y_recuerda_que_no_hay_mas(tmp_path):
    H = 3600000
    h = Historico(directorio=tmp_path, net=True)

    def descargar(sym, tf, n, desde):
        return "binance:X", [{"t": t, "o": 1, "h": 2, "l": 1, "c": 1.5, "v": 1} for t in range(4900 * H, 5000 * H, H)]   # poca historia
    h._descargar = descargar
    h.actualizar("HYPE", "1h", 5000 * H + 10)
    assert not list(tmp_path.glob("*.tmp"))                     # no queda archivo temporal
    h2 = Historico(directorio=tmp_path, net=True)               # nuevo arranque: sabe que el mercado no tiene más historia
    assert h2.cargar("HYPE", "1h")["tope"] is True


# ---------- proyección ----------
def _velas(n=700, seed=1, glitch_inicio=False):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.standard_normal(n) * .01))
    rows = [{"t": i * 14400000, "o": float(c[i - 1] if i else c[0]), "h": float(c[i] * 1.004), "l": float(c[i] * .996), "c": float(c[i]), "v": 1.0} for i in range(n)]
    if glitch_inicio:                                            # como la primera semana de ZEC en Kraken: 100000 → 880
        rows[0].update(o=100000.0, h=215000.0, l=400.0, c=880.0)
        for i, r in enumerate(rows[1:6], 1):
            r.update(c=r["c"] * (1 + .0))
    return rows


def test_limpiar_quita_velas_imposibles_y_corrige_maximos():
    filas = [{"t": 1, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1}, {"t": 2, "o": 10, "h": float("nan"), "l": 9, "c": 10, "v": 1},
             {"t": 3, "o": 0, "h": 1, "l": 0, "c": 1, "v": 1}, {"t": 4, "o": 10, "h": 9, "l": 11, "c": 10, "v": float("inf")}]
    out = limpiar(filas)
    assert [r["t"] for r in out] == [1, 4]
    assert out[1]["h"] >= out[1]["l"] and out[1]["v"] == 0.0


def test_una_vela_absurda_al_principio_no_arrastra_la_volatilidad():
    limpia = _velas()
    sucia = _velas(glitch_inicio=True)
    sa, _ = volatilidad(np.array([r["c"] for r in limpia]))
    sb, _ = volatilidad(np.array([r["c"] for r in sucia]))
    assert abs(sb[-1] / sa[-1] - 1) < .02                       # a las 700 velas, la mecha de la primera no deja huella
    assert sb[150] < 2 * sa[150]                                # y a las 150 tampoco había desbordado la estimación


def test_la_calibracion_mide_la_actividad_de_las_velas_recientes():
    activas = calibrar(_velas(), "4h")
    assert activas["activa"] > .99
    planas = _velas()
    for r in planas[-450:]:
        r["v"] = 0.0
    assert calibrar(planas, "4h")["activa"] < .35
