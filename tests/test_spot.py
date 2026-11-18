"""Decisión spot, frescura del precio y robustez traídas de la versión del Mac (18 nov 2026). El registro SQLite no se usa en la nube."""
import json
from unittest.mock import patch

from cripto import servicio
from cripto.decision import decision
from cripto.envivo import Flujo
from cripto.historico import Historico
from cripto.noticias import reaccion
from cripto.validacion import con_intervalo
from test_paridad import FakeKraken, FIX


def caso():
    fx = json.loads(FIX.read_text())
    sv = servicio.Servicio(FakeKraken(fx), net=False)
    sv.pairs, sv.tk = fx["pairs"], fx["ticker"]
    sv.data["BTC"] = sv.run("BTC", now=fx["now"])
    return sv, fx["now"]


def test_precio_caducado_queda_fuera_de_la_decision():
    sv, t = caso()
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    with patch.object(servicio, "now_ms", return_value=t+3600000):
        sv.tiempo_real()
    o = sv.data["BTC"]
    assert o["precio_t"] == t and o["decision"]["accion"] == "ND"
    assert o["prev"]["obsoleta"] and not o["proy"]
    assert json.loads(sv.tick_payload())["coins"]["BTC"]["decision"] == servicio.ligero(o["decision"])


def test_cache_y_extremos_intrabar():
    sv, t = caso()
    o = sv.data["BTC"]
    with patch.object(servicio, "now_ms", return_value=t):
        p = o["price"]
        o["price"] = p * 1.02
        sv.tiempo_real()
        alta = o["vivo"]["tf"]["1h"]["vela"]["h"]
        o["price"] = p
        sv.tiempo_real()
        assert o["vivo"]["tf"]["1h"]["vela"]["h"] == alta
        with patch.object(servicio, "analyze", wraps=servicio.analyze) as contar:
            sv.tiempo_real()
            assert contar.call_count == 0


def test_crc_rechazado_no_cuenta_y_requiere_snapshot():
    s = "BTC/EUR"
    fl = Flujo([s], {s:(1,1)})
    fl.conectado = True
    book = fl.libros[s]
    d = dict(asks=[dict(price=101.,qty=1.)],bids=[dict(price=100.,qty=30.)],checksum=1)
    assert not book.apply(d,True)
    assert fl.micro(s)["libro_pct"] is None
    assert not book.apply({},False)
    assert book.apply({k:v for k,v in d.items() if k!='checksum'},True)
    assert fl.micro(s)["libro_pct"] is not None
    fl.conectado = False
    assert fl.micro(s)["libro_pct"] is None


def test_noticia_exige_datos_posteriores_y_apertura():
    rows = [dict(t=i*3600000,o=p,c=q) for i,(p,q) in enumerate([(100,100),(100,120),(120,119)])]
    assert reaccion(dict(t=10800000,dir=1),[],rows,18000000) is None
    r = reaccion(dict(t=3600001,dir=1),[],rows,10800001)
    assert round(r["r60"],4) == 20


def test_cambio_de_fuente_reconstruye_sin_mezclar(tmp_path):
    h = Historico(tmp_path)
    def vela(t, precio):
        return dict(t=t,o=precio,h=precio,l=precio,c=precio,v=1)
    h.mem["BTC","1m"] = dict(rows=[vela(60000,20)],fuente="antigua",tope=True)
    with patch.object(h,"_descargar",side_effect=[("nueva",[vela(120000,200)]),
                                                  ("nueva",[vela(60000,201),vela(120000,200)])]):
        assert [r["c"] for r in h.actualizar("BTC","1m",600000)] == [201,200]



def test_incertidumbre_y_contradiccion():
    o=dict(precio_t=5000,prev={"cobertura":.22,"dir":"alcista"},tm={"v":"C"})
    assert decision(o,6000)["accion"]=="ND"
    o["prev"].update(cobertura=.8,dir="bajista")
    assert decision(o,6000)["accion"]=="E"
    assert con_intervalo({"vd":["AL","Ventaja modesta"]},{},{"lo":.01})["vd"][0]=="NE"


def test_libro_integro_confirma_precio_de_par_tranquilo():
    import time
    s = "BTC/EUR"
    fl = Flujo([s], {s: (1, 1)})
    fl.conectado, fl.ultimo = True, time.time()
    d = dict(asks=[dict(price=101., qty=1.)], bids=[dict(price=100., qty=3.)])
    assert fl.libros[s].apply(d, True)
    assert fl.confirmacion(s, 100.5) == fl.ultimo              # dentro de la horquilla: vigente
    assert fl.confirmacion(s, 103.) is None                    # fuera del libro: no se confirma
    fl.ultimo -= 60
    assert fl.confirmacion(s, 100.5) is None                   # flujo parado
    fl.ultimo += 60
    fl.libros[s].valido = False
    assert fl.confirmacion(s, 100.5) is None                   # CRC incorrecto
    fl.libros[s].valido, fl.conectado = True, False
    assert fl.confirmacion(s, 100.5) is None                   # sin conexión


def test_tiempo_real_usa_la_confirmacion_del_libro():
    sv, t = caso()
    o = sv.data["BTC"]

    class Fl:
        ticker = {}
        def precio(self, s): return None
        def confirmacion(self, s, p): return (t + 3600000 - 5000) / 1000
        def micro(self, s): return None

    sv.flujo = Fl()
    with patch.object(servicio, "now_ms", return_value=t + 3600000):
        sv.tiempo_real()
    assert o["precio_t"] == t + 3600000 - 5000 and o["decision"]["accion"] != "ND"


def test_cotizacion_recibida_durante_el_ciclo_no_caduca():
    sv, t = caso()
    o = sv.data["BTC"]

    class Fl:     # el ticker llega 30 ms después de empezar el ciclo
        ticker = {"BTC/EUR": {"t": (t + 30) / 1000}}
        def precio(self, s): return o["d1"]["last"] if s == "BTC/EUR" else None
        def confirmacion(self, s, p): return None
        def micro(self, s): return None

    sv.flujo = Fl()
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    assert o["precio_t"] <= t and o["decision"]["motivo"] != "Cotización pendiente de renovar"


def test_nube_no_guarda_registro_de_decisiones():
    import pathlib
    assert "sv.registro = None" in (pathlib.Path(__file__).resolve().parents[1] / "nube.py").read_text()
