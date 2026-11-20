"""Evidencias de todas las monedas del catálogo de principio a fin: emisión horaria inmutable, verificación con velas de Kraken, huecos, datos
caducados, reconexión, reinicio, avisos trazables y compatibilidad con una base de decisiones previas. Todo con bases temporales."""
import json
import pathlib
import shutil
import sqlite3
import subprocess
from unittest.mock import patch

import pytest

from cripto import evidencias as EVM
from cripto import servicio
from cripto.catalogo import SIMBOLOS
from cripto.avisos import CONTINUIDAD, ENFRIA, PERIODO_CONTEXTO, Avisos
from cripto.decision import Registro
from cripto.envivo import Flujo
from cripto.evidencias import DIA, HORA, MIN, Almacen, evaluar_emision
from estudios import evaluar_spot
from test_paridad import FakeKraken, FIX

RAIZ = pathlib.Path(__file__).resolve().parents[1]
T0 = 1_795_000_000_000 + 30_500          # emisión a mitad de un minuto (para probar los minutos parciales)


# ---------- utilidades ----------
def servicio_con(tmp_path, *simbolos):
    fx = json.loads(FIX.read_text())
    alm, av = Almacen(tmp_path / "e.sqlite3"), Avisos(tmp_path / "e.sqlite3")
    sv = servicio.Servicio(FakeKraken(fx), net=False, almacen=alm, avisos=av)
    sv.pairs, sv.tk = fx["pairs"], fx["ticker"]
    for s in simbolos:
        sv.data[s] = sv.run(s, now=fx["now"])
        # en la foto real, el último negociado de ETH, XRP y SOL está fuera de la horquilla del ticker REST: no se da por
        # vigente hasta que lo confirme el libro en vivo (aquí se simula esa confirmación)
        sv.data[s]["_precio_t"] = sv.data[s]["_precio_t"] or fx["now"]
        sv.calibrar_moneda(s)
    return sv, fx["now"]


def emision(t0=T0, s="BTC", precio=100.0):
    return {"version": EVM.VERSION_CALCULO, "simbolo": s, "hora_utc": t0 - t0 % HORA, "t_emision": t0, "t_vence": t0 + DIA,
            "velas": "1h", "horizonte_h": 24, "mercado": {"bolsa": "Kraken", "par": f"{s}/EUR", "fuente_precio": "WebSocket"},
            "precio": precio, "b50": [98.0, 102.0], "b80": [95.0, 105.0],
            "niveles": [{"id": "S1", "lado": "sube", "borde": 104.0, "p": .3}, {"id": "E1", "lado": "baja", "borde": 96.0, "p": .4}],
            "frescura": {}, "publicado": {},
            "referencia": {"b50": [99.0, 101.0], "b80": [97.0, 103.0], "niveles": [{"id": "S1", "p": .1}, {"id": "E1", "p": .1}]},
            "experimental": None}


def velas(t0, t1, precio=100.0, cambios=None, n=2):
    """Velas de 1 min planas a `precio` entre t0 y t1, con cambios {minuto: dict(...)}."""
    out = []
    for m in range(t0 - t0 % MIN, t1, MIN):
        v = {"t": m, "o": precio, "h": precio, "l": precio, "c": precio, "v": 1.0, "n": n}
        v.update((cambios or {}).get(m, {}))
        out.append(v)
    return out


def guardar(alm, s, filas, ahora):
    alm.guardar_rest(s, filas, filas[-1]["t"], ahora)


# ---------- emisión desde el servicio ----------
@pytest.mark.parametrize("s", SIMBOLOS)
def test_emision_horaria_real_inmutable_y_sin_duplicar(tmp_path, s):
    sv, t = servicio_con(tmp_path, s)
    o = sv.data[s]
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    with patch.object(servicio, "now_ms", return_value=t + 1000):
        sv.tiempo_real()                                     # misma hora: no se duplica
    with sqlite3.connect(tmp_path / "e.sqlite3") as db:
        filas = db.execute("SELECT simbolo, hora, contenido, huella FROM proyecciones").fetchall()
        assert len(filas) == 1
        em = json.loads(EVM.desempaquetar(filas[0][2]))
        assert isinstance(filas[0][2], bytes)                    # guardada comprimida
        assert em["simbolo"] == s and em["hora_utc"] == t - t % HORA and em["t_vence"] == em["t_emision"] + DIA
        assert em["velas"] == "1h" and em["horizonte_h"] == 24 and em["version"] == EVM.VERSION_CALCULO
        assert em["mercado"] == {"bolsa": "Kraken", "par": servicio.WSV2[s], "fuente_precio": "REST"}
        # lo guardado es exactamente lo que se envía a la pantalla
        assert em["publicado"] == json.loads(json.dumps(servicio.ligero(servicio.compacto(o["proy"]["1h"]))))
        assert em["b50"] == em["publicado"]["b50"] and em["b80"] == em["publicado"]["b80"]
        assert {g["id"] for g in em["niveles"]} == {g["id"] for g in em["publicado"]["niveles"]}
        assert all({"borde", "lado", "p"} <= set(g) for g in em["niveles"])
        assert em["frescura"]["precio_t"] == t and em["frescura"]["edad_s"] == 0
        assert em["referencia"]["n"] > 500 and em["experimental"]["b80"][0] < em["precio"] < em["experimental"]["b80"][1]
        assert EVM.huella(EVM.desempaquetar(filas[0][2])) == filas[0][3]   # huella del texto canónico
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("UPDATE proyecciones SET precio=1")
    # la hora siguiente con el precio caducado: no se emite y queda anotado el motivo
    with patch.object(servicio, "now_ms", return_value=t + HORA):
        sv.tiempo_real()
    sv.ciclo_evidencias(t + 2 * HORA)
    with sqlite3.connect(tmp_path / "e.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM proyecciones").fetchone()[0] == 1
        assert "no vigente" in db.execute("SELECT motivo FROM omisiones_proy").fetchone()[0]
    assert sv.data[s]["fuentes"]["suspendidos"]           # precio caducado: avisos y emisión suspendidos


def test_referencia_solo_usa_velas_cerradas_antes_de_emitir(tmp_path):
    sv, t = servicio_con(tmp_path, "BTC")
    o = sv.data["BTC"]
    raw = o["_raw"]["1h"]
    niveles = [{"id": "S1", "lado": "sube", "borde": o["price"] * 1.02}]
    a = EVM.referencia(raw, o["price"], niveles, t)
    futuras = [dict(raw[-1], t=raw[-1]["t"] + k * HORA, c=raw[-1]["c"] * 3, h=raw[-1]["c"] * 3) for k in range(1, 30)]
    assert EVM.referencia(raw + futuras, o["price"], niveles, t) == a   # lo posterior a la emisión no cambia nada
    assert a["hasta"] <= t


# ---------- verificación ----------
def test_rango_acertado_y_minutos_parciales_excluidos(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    em = emision()
    alm.emitir(em)
    m_emision, m_vence = T0 - T0 % MIN, (T0 + DIA) - (T0 + DIA) % MIN
    # una operación a 110 en el minuto de la emisión (antes de emitir) y a 90 en el minuto del vencimiento (después)
    filas = velas(T0 - 10 * MIN, T0 + DIA + 10 * MIN, cambios={m_emision: {"h": 110.0}, m_vence: {"l": 90.0, "c": 90.0},
                                                                m_vence - MIN: {"c": 101.5}})
    guardar(alm, "BTC", filas, T0 + DIA + 11 * MIN)
    assert alm.evaluar_pendientes(T0 + DIA - MIN) == 0              # aún no vence
    assert alm.evaluar_pendientes(T0 + DIA + 11 * MIN) == 1
    d = alm.detalle("BTC", em["hora_utc"])
    r = d["resultado"]
    assert d["estado"] == "verificada" and d["integra"]
    assert r["precio_final"] == 101.5 and r["t_precio_final"] == m_vence - MIN
    assert r["publicada"] == {"dentro50": True, "dentro80": True}
    assert r["referencia"] == {"dentro50": False, "dentro80": True}
    assert not any(g["tocado"] for g in r["niveles"])              # ni el 110 anterior ni el 90 posterior cuentan
    assert r["ventana"] == [m_emision + MIN, m_vence]


def test_rango_fallido_y_toque_observado_con_operacion(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    em = emision()
    alm.emitir(em)
    m = T0 - T0 % MIN
    filas = velas(T0 - 5 * MIN, T0 + DIA + 5 * MIN, cambios={
        m + 60 * MIN: {"h": 104.5},                                   # toca S1 con operaciones
        m + 90 * MIN: {"l": 80.0, "n": 0, "v": 0.0},                  # minuto sin operaciones: no acredita toque
        **{m + k * MIN: {"c": 106.0, "o": 106.0, "h": 106.0, "l": 106.0} for k in range(1000, 1500)}})
    guardar(alm, "BTC", filas, T0 + DIA + 6 * MIN)
    alm.evaluar_pendientes(T0 + DIA + 6 * MIN)
    r = alm.detalle("BTC", em["hora_utc"])["resultado"]
    assert r["precio_final"] == 106.0
    assert r["publicada"] == {"dentro50": False, "dentro80": False}
    s1, e1 = (next(g for g in r["niveles"] if g["id"] == k) for k in ("S1", "E1"))
    assert s1["tocado"] and s1["t_toque"] == m + 60 * MIN
    assert not e1["tocado"] and e1["extremo"] == 100.0


def test_hueco_pendiente_mientras_rest_puede_rellenarlo_y_luego_sin_seguimiento(tmp_path):
    ruta = tmp_path / "e.sqlite3"
    alm = Almacen(ruta)
    em = emision()
    alm.emitir(em)
    hueco = T0 + 5 * HORA
    filas = velas(T0 - 5 * MIN, T0 + DIA + 5 * MIN)
    antes = [v for v in filas if v["t"] < hueco]
    despues = [v for v in filas if v["t"] >= hueco + 30 * MIN]
    guardar(alm, "BTC", antes, T0)
    guardar(alm, "BTC", despues, T0 + DIA)
    assert alm.evaluar_pendientes(hueco + 2 * HORA) == 0            # no ha vencido
    # el hueco de 1 min ya no se puede rellenar, pero las velas de 15 min sí (7,5 días): sigue pendiente
    assert alm.evaluar_pendientes(T0 + DIA + 5 * MIN) == 0
    # pasados 7 días sin velas de 15 min: sin seguimiento, con las dos causas
    assert alm.evaluar_pendientes(T0 + 8 * DIA, forzar=True) == 1
    d = alm.detalle("BTC", em["hora_utc"])
    assert d["estado"] == "sin_seguimiento" and "30 min" in d["motivo"] and "Servicio apagado" in d["motivo"]
    assert "velas de 15 min" in d["motivo"]
    assert d["resultado"]["huecos"][0][1] - d["resultado"]["huecos"][0][0] >= 29 * MIN
    # un hueco reciente (rellenable por REST) deja la emisión pendiente, no la da por perdida
    em2 = emision(T0 + HORA)
    alm.emitir(em2)
    filas2 = [v for v in velas(T0 + HORA, T0 + HORA + DIA + 5 * MIN) if not (T0 + HORA + DIA - 20 * MIN <= v["t"] < T0 + HORA + DIA - 10 * MIN)]
    res = evaluar_emision(em2, {v["t"]: v for v in filas2}, [(filas2[0]["t"], T0 + HORA + DIA - 20 * MIN - (T0 % MIN)),
                                                             (T0 + HORA + DIA - 10 * MIN, T0 + HORA + DIA + 5 * MIN)],
                          T0 + HORA + DIA + 4 * MIN)
    assert res is None


def velas15(t0, t1, precio=100.0, cambios=None):
    out = []
    for q in range(t0 - t0 % (15 * MIN), t1, 15 * MIN):
        v = {"t": q, "o": precio, "h": precio, "l": precio, "c": precio, "v": 1.0, "n": 3}
        v.update((cambios or {}).get(q, {}))
        out.append(v)
    return out


def test_app_abierta_un_rato_al_dia_se_verifica_con_velas_de_15_min(tmp_path):
    """Sin minutos de la ventana (la app estuvo cerrada), la emisión se comprueba con velas de 15 min de Kraken REST."""
    alm = Almacen(tmp_path / "e.sqlite3")
    em = emision()
    alm.emitir(em)
    q = lambda t: t - t % (15 * MIN)                                             # noqa: E731
    cambios = {q(T0 + 6 * HORA): {"h": 104.5, "l": 99.0},                        # toca S1 (104)
               q(T0 + 7 * HORA): {"n": 0, "l": 90.0},                            # sin operaciones: no cuenta como toque
               q(T0 + DIA) - 15 * MIN: {"c": 101.0}}                             # último tramo completo: precio final
    filas = velas15(T0 - HORA, T0 + DIA + HORA, cambios=cambios)
    assert alm.evaluar_pendientes(T0 + DIA + 10 * MIN) == 0                      # aún sin velas de 15 min
    alm.guardar_rest15("BTC", filas, filas[-1]["t"], T0 + DIA + HORA)
    assert alm.evaluar_pendientes(T0 + DIA + 2 * HORA, forzar=True) == 1
    d = alm.detalle("BTC", em["hora_utc"])
    r = d["resultado"]
    assert d["estado"] == "verificada" and r["version"] == EVM.VERSION_EVAL_15 and r["resolucion"] == "15m"
    assert r["precio_final"] == 101.0 and r["publicada"] == {"dentro50": True, "dentro80": True}
    toc = {g["id"]: g["tocado"] for g in r["niveles"]}
    assert toc == {"S1": True, "E1": False}
    # con los minutos completos se sigue usando la resolución de 1 min
    em2 = emision(T0 + HORA)
    alm.emitir(em2)
    guardar(alm, "BTC", velas(T0 + HORA - 5 * MIN, T0 + HORA + DIA + 5 * MIN), T0 + HORA + DIA + 5 * MIN)
    alm.evaluar_pendientes(T0 + HORA + DIA + 5 * MIN, forzar=True)
    assert alm.detalle("BTC", em2["hora_utc"])["resultado"]["version"] == EVM.VERSION_EVAL


def test_evaluador_idempotente_y_recupera_pendientes_al_reiniciar(tmp_path):
    ruta = tmp_path / "e.sqlite3"
    alm = Almacen(ruta)
    for k in range(3):
        alm.emitir(emision(T0 + k * HORA))
    guardar(alm, "BTC", velas(T0 - 5 * MIN, T0 + 3 * HORA + DIA + 5 * MIN), T0 + DIA)
    ahora = T0 + 2 * HORA + DIA + 5 * MIN
    del alm                                                          # «reinicio»: otra instancia sobre la misma base
    alm = Almacen(ruta)
    assert alm.evaluar_pendientes(ahora) == 3
    with sqlite3.connect(ruta) as db:
        foto = db.execute("SELECT * FROM resultados_proy ORDER BY hora").fetchall()
    for _ in range(3):
        assert Almacen(ruta).evaluar_pendientes(ahora + HORA) == 0
    with sqlite3.connect(ruta) as db:
        assert db.execute("SELECT * FROM resultados_proy ORDER BY hora").fetchall() == foto
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("UPDATE resultados_proy SET estado='x'")
    assert not alm.emitir(emision(T0))                             # la misma hora no se emite dos veces


def test_resumen_estadisticas_y_ventanas_diarias(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    for k in range(30):
        alm.emitir(emision(T0 + k * HORA))
    guardar(alm, "BTC", velas(T0 - 5 * MIN, T0 + 30 * HORA + DIA + 5 * MIN, cambios={T0 - T0 % MIN + 120 * MIN: {"h": 104.2}}), T0)
    alm.latido(T0)
    alm.latido(T0 + 30 * HORA + DIA + 5 * MIN)
    alm.evaluar_pendientes(T0 + 30 * HORA + DIA + 5 * MIN)
    r = alm.resumen("BTC", T0 + 30 * HORA + DIA + 5 * MIN)
    assert r["cuenta"] == {"pendientes": 0, "verificadas": 30, "sin_seguimiento": 0}
    p = r["todas"]["publicada"]
    assert p["n"] == 30 and p["cob50"] == 1.0 and p["anch80"] == pytest.approx(10.0)
    assert r["diarias"]["publicada"]["n"] == 2                    # 30 emisiones horarias = 2 ventanas que no se solapan
    t = {x["de"]: x for x in p["toques"]["tramos"]}
    assert t[.3]["n"] == 60 and t[.3]["frecuencia"] == pytest.approx(2 / 60)   # S1 (p .3) y E1 (p .4); S1 tocado en 2 emisiones
    assert r["todas"]["referencia"]["n"] == 30 and r["todas"]["experimental"]["n"] == 0
    assert r["horas_apagado"] == 0 and len(r["lista"]) == 30


def test_diarias_no_se_solapan():
    xs = [{"simbolo": s, "t_emision": T0 + k * HORA} for k in range(60) for s in ("BTC", "ETH")]
    d = EVM.diarias(xs)
    for s in ("BTC", "ETH"):
        ts = [x["t_emision"] for x in d if x["simbolo"] == s]
        assert ts == [T0, T0 + DIA, T0 + 2 * DIA]


# ---------- flujo en vivo: sin operaciones frente a hueco; reconexión ----------
def _iso(ms):
    import datetime
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def test_minuto_sin_operaciones_frente_a_hueco_de_conexion(tmp_path):
    s = "BTC/EUR"
    fl = Flujo([s], {s: (1, 8)})
    base = T0 - T0 % MIN + 10 * MIN
    with patch("cripto.envivo.time.time", return_value=(base - 20_000) / 1000):
        fl.conectado = True
        fl._msg({"channel": "ohlc", "type": "snapshot", "data": [{"symbol": s, "interval_begin": _iso(base - MIN), "open": 1,
                                                                   "high": 1, "low": 1, "close": 1, "volume": 1, "trades": 1}]})
    with patch("cripto.envivo.time.time", return_value=(base + 2 * MIN + 5000) / 1000):
        fl._msg({"channel": "ohlc", "type": "update", "data": [{"symbol": s, "interval_begin": _iso(base + 2 * MIN), "open": 2,
                                                                 "high": 2, "low": 2, "close": 2, "volume": 3, "trades": 4}]})
        fl._msg({"channel": "trade", "type": "update", "data": [{"symbol": s, "price": 2.0, "qty": .5, "side": "buy",
                                                                  "timestamp": _iso(base + 2 * MIN + 4000), "trade_id": 7}]})
    with patch("cripto.envivo.time.time", return_value=(base + 4 * MIN) / 1000):
        fl._msg({"channel": "heartbeat"})
    fl._desconectado()                                              # se corta la conexión
    assert fl.cobertura_ws(s, base + 9 * MIN) == [(base - 20_000, base + 4 * MIN)]
    assert fl.ultima_op[s]["t"] == base + 2 * MIN + 4000 and fl.ultima_op[s]["p"] == 2.0
    alm = Almacen(tmp_path / "e.sqlite3")
    limite = base + 9 * MIN
    alm.guardar_ws("BTC", fl.tomar_cerradas(s, limite), fl.cobertura_ws(s, limite), limite, limite)
    est = {x["t"]: x["e"] for x in alm.minutos("BTC", 12, base + 9 * MIN)}
    assert est[base - MIN] == "ops"                                 # vela del snapshot
    assert est[base] == est[base + MIN] == est[base + 3 * MIN] == "sin_ops"   # canal activo, sin operaciones
    assert est[base + 2 * MIN] == "ops"
    assert est[base + 4 * MIN] == est[base + 8 * MIN] == "hueco"     # después de la desconexión
    vol = fl.tomar_agregados(s, limite)
    assert vol and vol[0]["n"] == 1 and vol[0]["compra"] == 1.0
    # tras reconectar, el REST de Kraken rellena el hueco y conserva su procedencia
    alm.guardar_rest("BTC", velas(base + 4 * MIN, base + 9 * MIN, 2.0, n=0), base + 8 * MIN, base + 9 * MIN)
    est = {x["t"]: x for x in alm.minutos("BTC", 12, base + 9 * MIN)}
    assert est[base + 5 * MIN]["e"] == "sin_ops" and est[base + 5 * MIN]["f"] == "rest"
    assert est[base + 2 * MIN]["f"] == "ws"


def test_libro_capturado_solo_si_verificado(tmp_path):
    import time
    s = "ETH/EUR"
    fl = Flujo([s], {s: (2, 8)})
    assert fl.profundidad(s) is None
    fl.conectado = True
    fl.libros[s].apply({"asks": [{"price": 101.0, "qty": 2.0}], "bids": [{"price": 100.0, "qty": 3.0}]}, True)
    f = fl.profundidad(s)
    assert f["bid"] == 100.0 and f["ask"] == 101.0 and f["compra_1"] == 300.0 and f["venta_1"] == 202.0
    alm = Almacen(tmp_path / "e.sqlite3")
    ahora = int(time.time() * 1000)
    alm.guardar_libro("ETH", f, ahora)
    alm.guardar_libro("ETH", f, ahora - ahora % EVM.LIBRO_CADA + EVM.LIBRO_CADA - 1)   # mismo tramo de 5 min: no se repite
    assert alm.captura("ETH")["libro"]["n"] == 1
    with sqlite3.connect(tmp_path / "e.sqlite3") as db:                                 # en columnas, no en JSON
        assert db.execute('SELECT bid, ask, niveles_compra, "compra_1" FROM libro').fetchone() == (100.0, 101.0, 1, 300.0)
    # con 25 niveles muy juntos el libro no llega a ±0,5 %: esa banda se deja vacía en vez de truncarla
    fl.libros[s].apply({"asks": [{"price": 100.01 + i * .01, "qty": 1.0} for i in range(30)],
                        "bids": [{"price": 100.0 - i * .01, "qty": 1.0} for i in range(30)]}, True)
    f = fl.profundidad(s)                                    # 25 niveles: alcanzan ±0,24 %
    assert f["niveles"] == [25, 25] and .2 < f["alcance_compra_pct"] < .5
    assert f["compra_0,1"] == pytest.approx(sum(100.0 - i * .01 for i in range(10)))
    assert f["compra_0,5"] is None and f["venta_1"] is None
    fl._desconectado()
    assert fl.profundidad(s) is None


# ---------- avisos ----------
def _op(p, t):
    return {"p": p, "t": t - 100, "recibido": t - 60, "id": t}


def test_oscilar_alrededor_de_un_nivel_no_repite_avisos(tmp_path):
    av = Avisos(tmp_path / "e.sqlite3")
    av.anadir_nivel("BTC", 100.0, "prueba", T0)
    ctx = lambda: {"precio": 1}                                      # noqa: E731
    t = T0
    serie = [101, 100.05, 99.95, 100.02, 99.85, 100.04, 100.12, 99.8, 100.2, 99.7]
    emitidos = []
    for p in serie:
        t += 1000
        emitidos += av.observar("BTC", _op(p, t), {"accion": "E"}, True, t, ctx)
    assert len(emitidos) == 1                                        # solo el primer cruce real (99,85); el resto, enfriamiento
    a = av.lista()[0] if av.lista()[0]["tipo"] == "cruce" else av.lista()[1]
    assert a["detalle"]["de"] == "arriba" and a["detalle"]["a"] == "abajo"
    assert a["t_dato"] < a["t_llegada"] <= a["t_deteccion"] <= a["t_emision"] and a["t_entrega"] is None
    # pasado el enfriamiento, un cruce nuevo sí avisa
    t += ENFRIA
    for p in (99.7, 100.3):
        t += 1000
        emitidos += av.observar("BTC", _op(p, t), {"accion": "E"}, True, t, ctx)
    assert len(emitidos) == 2


def test_datos_caducados_reconexion_y_reinicio_no_disparan(tmp_path):
    ruta = tmp_path / "e.sqlite3"
    av = Avisos(ruta)
    av.anadir_nivel("ETH", 2000.0, "", T0)
    ctx = lambda: {}                                                 # noqa: E731
    t = T0
    assert not av.observar("ETH", _op(2010, t), {"accion": "E"}, True, t, ctx)       # se toma el lado en silencio
    # datos caducados: aunque el precio cruce, no se evalúa
    assert not av.observar("ETH", _op(1990, t + 1000), {"accion": "ND"}, False, t + 1000, ctx)
    # reconexión tras más de 120 s: el cruce ocurrió durante el hueco -> se registra como omitido, sin aviso
    t2 = t + CONTINUIDAD + 5000
    assert not av.observar("ETH", _op(1990, t2), {"accion": "E"}, True, t2, ctx)
    om = [a for a in av.lista() if a["estado"] == "omitido"]
    assert len(om) == 1 and "hueco" in om[0]["detalle"]["motivo"]
    assert not av.entregar(om[0]["id"], "pantalla", t2)             # un omitido no se entrega
    # cruce real con datos continuos -> un aviso
    assert len(av.observar("ETH", _op(2010, t2 + 1000), {"accion": "E"}, True, t2 + 1000, ctx)) == 1
    # reconexión breve (< 120 s) sin cambio de lado -> nada
    assert not av.observar("ETH", _op(2011, t2 + 60_000), {"accion": "E"}, True, t2 + 60_000, ctx)
    # reinicio: otra instancia lee el estado guardado y no repite el aviso
    av2 = Avisos(ruta)
    assert not av2.observar("ETH", _op(2012, t2 + 90_000), {"accion": "E"}, True, t2 + 90_000, ctx)
    assert len([a for a in av2.lista() if a["estado"] == "emitido"]) == 1


def test_cambio_de_contexto_sostenido(tmp_path):
    av = Avisos(tmp_path / "e.sqlite3")
    ctx = lambda: {"precio": 5}                                       # noqa: E731
    d = lambda a: {"accion": a, "motivo": "m", "precio_t": 1}         # noqa: E731
    t = T0
    av.observar("BTC", None, d("E"), True, t, ctx)
    out = []
    for k in range(1, 200):                                           # C durante 199 s
        out += av.observar("BTC", None, d("C"), True, t + k * 1000, ctx)
    out += av.observar("BTC", None, d("ND"), False, t + 200_000, ctx)  # dato caducado: interrumpe la cuenta
    for k in range(201, 201 + PERIODO_CONTEXTO // 1000):
        out += av.observar("BTC", None, d("C"), True, t + k * 1000, ctx)
    assert out == []                                                  # 299 s seguidos tras la interrupción: aún no
    out += av.observar("BTC", None, d("C"), True, t + (201 + PERIODO_CONTEXTO // 1000) * 1000, ctx)
    assert len(out) == 1
    a = av.lista()[0]
    assert a["tipo"] == "contexto" and a["detalle"]["de"] == "E" and a["detalle"]["a"] == "C" and a["detalle"]["contexto"]
    for k in range(5):                                                # se mantiene: no se repite
        assert not av.observar("BTC", None, d("C"), True, t + 600_000 + k * 1000, ctx)
    assert av.entregar(a["id"], "pantalla", t + 700_000) and not av.entregar(a["id"], "sistema", t + 700_001)
    assert Avisos(tmp_path / "e.sqlite3").lista()[0]["t_entrega"] == t + 700_000


def test_servicio_avisa_con_operacion_y_no_con_precio_caducado(tmp_path):
    sv, t = servicio_con(tmp_path, "BTC")
    o = sv.data["BTC"]
    nivel = o["price"] * 1.01
    sv.avisos.anadir_nivel("BTC", nivel, "", t)

    class Fl:
        conectado, ultimo, libros = True, t / 1000, {}
        ticker = {}
        ultima_op = {}
        def precio(self, s): return None
        def confirmacion(self, s, p): return None
        def micro(self, s): return None

    sv.flujo = Fl()
    for k, p in enumerate((nivel * .99, nivel * 1.005)):
        Fl.ultima_op = {"BTC/EUR": _op(p, t + k * 1000)}
        with patch.object(servicio, "now_ms", return_value=t + k * 1000):
            sv.tiempo_real()
    assert [a["tipo"] for a in sv.avisos.lista() if a["estado"] == "emitido"] == ["cruce"]
    assert sv.av_ultimo == sv.avisos.ultimo_id()
    a = sv.avisos.lista()[0]
    assert a["detalle"]["contexto"]["procedencia"]["par"] == "BTC/EUR"
    # precio caducado: una operación al otro lado no dispara nada
    Fl.ultima_op = {"BTC/EUR": _op(nivel * .98, t + 200_000)}
    with patch.object(servicio, "now_ms", return_value=t + 200_000):
        sv.tiempo_real()
    assert len([a for a in sv.avisos.lista() if a["estado"] == "emitido"]) == 1
    assert "cruce de niveles" in o["fuentes"]["suspendidos"]


# ---------- de los datos de entrada a la pestaña Datos ----------
@pytest.mark.parametrize("s", SIMBOLOS)
def test_flujo_completo_hasta_la_pantalla(tmp_path, s):
    sv, t = servicio_con(tmp_path, s)
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    alm = sv.evid
    hora = t - t % HORA
    em = alm.detalle(s, hora)["emision"]
    p0 = em["precio"]
    guardar(alm, s, velas(t - 5 * MIN, t + DIA + 5 * MIN, p0), t + DIA + 5 * MIN)
    sv.ciclo_evidencias(t + DIA + 5 * MIN)
    r = alm.resumen(s, t + DIA + 5 * MIN)
    assert r["cuenta"]["verificadas"] == 1 and r["lista"][0]["estado"] == "verificada" and r["lista"][0]["dentro50"]
    d = alm.detalle(s, hora)
    assert d["integra"] and d["resultado"]["precio_final"] == p0 and d["serie"]
    assert json.dumps(r, allow_nan=False) and json.dumps(d, allow_nan=False)   # la API lo puede servir tal cual


# ---------- compatibilidad con una base con decisiones previas ----------
def test_abre_base_con_decisiones_previas(tmp_path):
    origen = RAIZ / ".copia_previa_evidencias" / "decisiones.sqlite3"
    ruta = tmp_path / "previa.sqlite3"
    if origen.exists():
        shutil.copy(origen, ruta)
    else:                                                             # sin copia previa: una base con el esquema antiguo
        with sqlite3.connect(ruta) as db:
            db.execute("CREATE TABLE decisiones (simbolo TEXT, t INTEGER, detalle TEXT, PRIMARY KEY(simbolo,t))")
            db.execute("CREATE TABLE precios (simbolo TEXT, minuto INTEGER, t INTEGER, precio REAL, PRIMARY KEY(simbolo,minuto))")
            db.execute("INSERT INTO decisiones VALUES ('ETH', 1, ?)", (json.dumps({"accion": "E", "zona": [1, 2], "precio": 1.5}),))
    with sqlite3.connect(ruta) as db:
        antes = db.execute("SELECT count(*), coalesce(sum(t), 0) FROM decisiones").fetchone()
        precios = db.execute("SELECT count(*) FROM precios").fetchone()
    Almacen(ruta)
    Avisos(ruta)
    Almacen(ruta)                                                     # abrir dos veces no rompe nada
    with sqlite3.connect(ruta) as db:
        assert db.execute("SELECT count(*), coalesce(sum(t), 0) FROM decisiones").fetchone() == antes
        assert db.execute("SELECT count(*) FROM precios").fetchone() == precios
        r = evaluar_spot.informe(db, 2e12, .001)
    assert r["total"] == antes[0]
    reg = Registro(ruta)
    reg.guardar("BTC", {"price": 10.0}, {"accion": "ND", "motivo": "x", "precio_t": 0, "zona": None}, 5)
    with sqlite3.connect(ruta) as db:
        assert db.execute("SELECT count(*) FROM decisiones").fetchone()[0] == antes[0] + 1


# ---------- evaluación spot: misma salida y mismos costes; casos excluidos con su motivo ----------
def test_evaluacion_spot_misma_salida_costes_y_exclusiones(tmp_path):
    ruta = tmp_path / "d.sqlite3"
    reg = Registro(ruta)
    t = T0
    o = {"price": 100.0, "precio_fuente": "WebSocket"}
    reg.guardar("BTC", o, {"accion": "E", "motivo": "a", "zona": [94.0, 96.0], "precio_t": t, "cobertura": 1}, t)
    reg.guardar("BTC", o, {"accion": "C", "motivo": "b", "zona": None, "precio_t": t + 1000}, t + 1000)        # otra decisión
    reg.guardar("BTC", o, {"accion": "E", "motivo": "b2", "zona": [94.0, 96.0], "precio_t": t + 2000}, t + 2000)  # misma hora
    reg.guardar("ETH", o, {"accion": "C", "motivo": "c", "zona": None, "precio_t": t}, t)                     # otra decisión
    reg.guardar("ETH", o, {"accion": "E", "motivo": "d", "zona": None, "precio_t": t + HORA}, t + HORA)        # sin zona
    reg.guardar("SOL", o, {"accion": "E", "motivo": "e", "zona": [1.0, 2.0], "precio_t": t}, t)               # sin precios
    q0 = t - t % (15 * MIN) + 15 * MIN
    with sqlite3.connect(ruta) as db:
        db.executescript(EVM.ESQUEMA)
        db.executemany("INSERT INTO velas_15m_rest VALUES(?,?,?,?,?,?,?,?,0)",
                       [("BTC", q, *((100.0, 100.0, 95.0, 100.0) if q == q0 + 30 * MIN else (110.0,) * 4 if q >= t + DIA else (100.0,) * 4), 1.0, 2)
                        for q in range(q0, t + DIA + 30 * MIN, 15 * MIN)])
        r = evaluar_spot.informe(db, t + DIA + 30 * MIN, .001)
    ex = r["excluidas"]
    assert ex["repetida en la misma hora"] == 1 and ex["otra decisión (no «Esperar»)"] == 2
    assert ex["«Esperar» sin zona"] == 1 and ex["esperando velas de 15 min"] == 1      # SOL: aún descargable
    with sqlite3.connect(ruta) as db:
        assert evaluar_spot.informe(db, t + 8 * DIA, .001)["excluidas"]["sin seguimiento suficiente"] == 1
    assert r["evaluadas"] == 1 and r["total"] == 6
    a = r["activos"]["BTC"]
    neto = lambda p: 110 * .999 / (p * 1.001) - 1                     # noqa: E731
    # orden límite en el borde superior (96): el tramo abre a 100 y baja a 95 → compra a 96
    assert a["espera"] == pytest.approx(neto(96)) and a["inmediata"] == pytest.approx(neto(100))
    assert a["repartida"] == pytest.approx((neto(100) + neto(100)) / 2) and a["zona_tocada"] == 1


def test_evaluacion_spot_no_pierde_la_espera_si_la_hora_empezo_con_otra_decision(tmp_path):
    """Al arrancar, la primera decisión de la hora suele ser «ND» (precio pendiente): la «Esperar» posterior de esa misma
    hora debe evaluarse, no descartarse como repetida."""
    ruta = tmp_path / "d.sqlite3"
    reg = Registro(ruta)
    t = T0
    o = {"price": 100.0, "precio_fuente": "WebSocket"}
    reg.guardar("BTC", o, {"accion": "ND", "motivo": "x", "zona": None, "precio_t": 0}, t)
    reg.guardar("BTC", o, {"accion": "E", "motivo": "a", "zona": [94.0, 96.0], "precio_t": t + 5000}, t + 5000)
    with sqlite3.connect(ruta) as db:
        db.executescript(EVM.ESQUEMA)
        db.executemany("INSERT INTO velas_15m_rest VALUES(?,?,?,?,?,?,?,?,0)",
                       [("BTC", q, 100.0, 100.0, 100.0, 100.0, 1.0, 2) for q in range(t - t % (15 * MIN), t + DIA + 30 * MIN, 15 * MIN)])
        r = evaluar_spot.informe(db, t + DIA + 30 * MIN, .001)
    assert r["evaluadas"] == 1 and "repetida en la misma hora" not in r["excluidas"]


def test_avisos_del_navegador_y_caducidad():
    r = subprocess.run(["node", str(RAIZ / "tests" / "alertas_spot.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_pagina_incluye_evidencias_y_avisos():
    html = (RAIZ / "web" / "index.html").read_text()
    for x in ("function evRender", "/api/evidencias?s=", "/api/avisos/entrega", "Diarias sin solapar", "function tabAvi",
              "velas ${TF} · horizonte", "Sin seguimiento", "Muestra pequeña"):
        assert x in html, x


def test_omision_de_la_hora_se_borra_si_luego_se_emite(tmp_path):
    """Tras un reinicio dentro de la misma hora, una omisión anotada antes no puede convivir con la emisión."""
    sv, t = servicio_con(tmp_path, "BTC")
    hora = t - t % HORA
    sv.evid.omision("BTC", hora, "precio no vigente", t)
    with patch.object(servicio, "now_ms", return_value=t + 60_000):
        sv.tiempo_real()
    with sv.evid._db() as db:
        assert not db.execute("SELECT 1 FROM omisiones_proy WHERE simbolo='BTC' AND hora=?", (hora,)).fetchone()
        assert db.execute("SELECT 1 FROM proyecciones WHERE simbolo='BTC' AND hora=?", (hora,)).fetchone()
