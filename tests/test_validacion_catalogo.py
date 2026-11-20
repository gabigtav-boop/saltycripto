"""Catálogo único y validación interna (18 nov 2026, noche): todas las monedas del catálogo pasan por el mismo flujo;
la lectura distingue frescura, cobertura e historial; los datos caducados no cuentan y la lectura se marca parcial; los
avisos se suspenden; el conjunto pesa igual cada activo; Datos queda compacto. Todo con bases temporales."""
import json
import pathlib
import re
import shutil
import sqlite3
from unittest.mock import patch

import pytest

from cripto import catalogo, envivo, evidencias as EVM, historico, noticias, sentimiento, servicio
from cripto.avisos import Avisos
from cripto.catalogo import CATALOGO, SIMBOLOS
from cripto.decision import Registro
from cripto.envivo import Flujo
from cripto.evidencias import DIA, HORA, MIN, Almacen
from test_evidencias import emision, guardar, servicio_con, velas

RAIZ = pathlib.Path(__file__).resolve().parents[1]
T0 = 1_795_000_000_000 + 30_500


# ---------- catálogo: una sola lista ----------
def test_todo_sale_del_catalogo():
    assert list(servicio.COINS) == list(SIMBOLOS) == list(servicio.WSV2) == list(servicio.CGID)
    assert set(sentimiento.KF_SYM) == set(historico.BIN_SYM) == set(historico.CB_SYM) == set(noticias.COIN_RX) == set(SIMBOLOS)
    assert EVM.ACTIVOS == SIMBOLOS and set(catalogo.web()) == set(SIMBOLOS)
    from cripto import avisos
    assert avisos.ACTIVOS == SIMBOLOS
    assert servicio.COINS["BTC"] == "XBT/EUR" and servicio.WSV2["BTC"] == "BTC/EUR" and catalogo.par("BTC") == "BTC/EUR"
    assert "PAXG" not in catalogo.CRIPTO and catalogo.web()["PAXG"] == {"n": "Oro (PAXG)", "a": "#d4a634", "i": "Au", "lab": "ORO", "cr": False, "mv": .5}


def test_ninguna_otra_lista_de_monedas():
    """Los identificadores por moneda solo aparecen en el catálogo (ni en Python ni en la página)."""
    marcas = ("XBT/EUR", "PF_XBTUSD", "pax-gold", "HYPEUSDT", "hyperliquid\"", "'#f7931a'", "\"#f7931a\"")
    for f in list((RAIZ / "cripto").glob("*.py")) + [RAIZ / "servidor.py", RAIZ / "web" / "index.html"]:
        if f.name == "catalogo.py":
            continue
        texto = f.read_text()
        assert not [m for m in marcas if m in texto], f.name
    html = (RAIZ / "web" / "index.html").read_text()
    assert "const COINS=window.CATALOGO" in html and '<script src="catalogo.js"></script>' in html   # relativa: la web publicada vive en /saltycripto/
    assert "EVA=Object.keys(COINS)" in html and "s!=='PAXG'" not in html and "SRVAV" not in html


# ---------- todas las monedas en el flujo (emisión → seguimiento → evaluación → historial) ----------
@pytest.mark.parametrize("s", SIMBOLOS)
def test_cada_moneda_del_catalogo_emite_evalua_y_cuenta(tmp_path, s):
    sv, t = servicio_con(tmp_path, s)
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    alm = sv.evid
    assert alm.emitida(s, t - t % HORA)
    em = alm.detalle(s, t - t % HORA)["emision"]
    assert em["mercado"]["par"] == CATALOGO[s]["ws"]
    guardar(alm, s, velas(t - 5 * MIN, t + DIA + 5 * MIN, em["precio"]), t + DIA + 5 * MIN)
    sv.ciclo_evidencias(t + DIA + 5 * MIN)
    h = sv.hist_ev["activos"][s]
    assert h["n_horarias"] == 1 and h["n_diarias"] == 1 and h["estado"] == "insuficiente"
    assert set(sv.hist_ev["activos"]) == set(SIMBOLOS)                    # todas las del catálogo con par en Kraken
    assert all(r["n_emisiones"] == 0 for x, r in sv.hist_ev["activos"].items() if x != s)


# ---------- base con registros previos ----------
def test_base_previa_migra_libro_conserva_todo_y_lee_emisiones_en_texto(tmp_path):
    origen = RAIZ / ".copia_previa_validacion" / "decisiones.sqlite3"
    if not origen.exists():
        pytest.skip("sin la copia previa de la base")
    ruta = tmp_path / "previa.sqlite3"
    shutil.copy(origen, ruta)
    with sqlite3.connect(ruta) as db:
        antes = {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("decisiones", "precios", "proyecciones", "velas_1m", "volumen_1m", "libro_1m", "sesiones")}
        tramos_antes = db.execute("SELECT count(*) FROM cobertura_1m").fetchone()[0]
    alm = Almacen(ruta)
    Almacen(ruta)                                                     # abrir dos veces no rompe ni duplica
    with sqlite3.connect(ruta) as db:
        for t in ("decisiones", "precios", "proyecciones", "velas_1m", "volumen_1m", "sesiones"):
            assert db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == antes[t], t
        assert db.execute("SELECT count(*) FROM libro").fetchone()[0] == antes["libro_1m"]      # migradas, ninguna perdida
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='libro_1m'").fetchone()
        assert db.execute("SELECT count(*) FROM cobertura_1m").fetchone()[0] <= tramos_antes  # tramos solapados juntos
    items = alm.items()
    assert len(items) == antes["proyecciones"] and all(x["estado"] == "pendiente" for x in items)
    for x in items:                                                    # las emisiones antiguas (texto) siguen íntegras
        assert alm.detalle(x["simbolo"], x["hora"])["integra"]
    h = alm.historial(2_000_000_000_000)
    assert set(h["activos"]) == set(SIMBOLOS) and h["global"]["estado"] == "insuficiente"


# ---------- historial: muestra pequeña, mismo peso por activo, versiones ----------
def _verificadas(alm, s, dias, dentro80, por_dia=1, version=EVM.VERSION_CALCULO, t0=T0):
    """Emisiones ya verificadas: `por_dia` horarias al día durante `dias` días; dentro80(i) dice si acertó el 80 %."""
    with sqlite3.connect(alm.ruta) as db:
        for d in range(dias):
            for h in range(por_dia):
                t = t0 + d * DIA + h * HORA
                em = dict(emision(t, s), version=version)
                tx = EVM.canonico(em)
                db.execute("INSERT INTO proyecciones VALUES (?,?,?,?,?,?,?,?)",
                           (s, em["hora_utc"], t, t + DIA, version, 100.0, EVM.empaquetar(tx), EVM.huella(tx)))
                r = {"estado": "verificada", "publicada": {"dentro50": d % 2 == 0, "dentro80": dentro80(d)},
                     "niveles": [{"id": "S1", "tocado": False}, {"id": "E1", "tocado": False}]}
                db.execute("INSERT INTO resultados_proy VALUES (?,?,?,?,?,?)", (s, em["hora_utc"], t + DIA, "verificada", None,
                                                                                EVM.canonico(r)))


def test_muestra_pequena_lo_dice_con_numeros_reales(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    _verificadas(alm, "SOL", 3, lambda d: True, por_dia=24)
    h = alm.historial(T0 + 10 * DIA)
    r = h["activos"]["SOL"]
    assert r["n_horarias"] == 72 and r["n_diarias"] == 3 and r["estado"] == "insuficiente"   # 72 horarias = 3 días
    lin = servicio.Servicio._mensajes_historial(h, T0 + 10 * DIA)
    assert lin == ["Historial aún insuficiente para valorar las proyecciones: 3 de 20 días verificados sin solapar "
                   "por activo (72 verificaciones horarias en total)."]


def test_conjunto_con_el_mismo_peso_por_activo_y_ventanas_sin_solapar(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    _verificadas(alm, "BTC", 60, lambda d: True, por_dia=24)          # muchos registros, todos dentro del 80 %
    _verificadas(alm, "ZEC", 24, lambda d: d % 2 == 0, por_dia=2)     # pocos, la mitad dentro
    h = alm.historial(T0 + 90 * DIA)
    A, G = h["activos"], h["global"]
    assert A["BTC"]["n_horarias"] == 1440 and A["BTC"]["n_diarias"] == 60           # las 24 horarias de un día cuentan 1
    assert A["ZEC"]["n_diarias"] == 24 and A["ZEC"]["cob80"] == .5
    assert G["activos"] == 2 and G["cob80"] == pytest.approx((1.0 + .5) / 2)        # no (60 + 12) / 84
    assert G["aportes"]["BTC"]["peso"] == G["aportes"]["ZEC"]["peso"] == .5
    assert A["ZEC"]["estado"] == "desviada" and A["ZEC"]["sentido"] == "estrecho"
    assert A["BTC"]["estado"] == "desviada" and A["BTC"]["sentido"] == "ancho"


def test_otra_version_de_calculo_no_se_mezcla(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    _verificadas(alm, "ETH", 25, lambda d: True, version="proy-vol-1h-24h/0")
    _verificadas(alm, "ETH", 5, lambda d: False, t0=T0 + 40 * DIA)
    r = alm.historial(T0 + 60 * DIA)["activos"]["ETH"]
    assert r["otras_versiones"] == 25 and r["n_diarias"] == 5 and r["cob80"] == 0 and r["estado"] == "insuficiente"
    res = alm.resumen("ETH", T0 + 60 * DIA)
    assert res["otras_versiones"] == 25 and res["todas"]["n"] == 5


def test_rango_desviado_avisa_en_estudio_y_en_datos(tmp_path):
    sv, t = servicio_con(tmp_path, "BTC")
    _verificadas(sv.evid, "XRP", 30, lambda d: d < 12, por_dia=2)     # 40 %: mucho menos del 80 % anunciado
    sv.ciclo_evidencias(T0 + 60 * DIA)
    sv.order, sv.off = ["BTC"], set(SIMBOLOS) - {"BTC"}
    c = sv.calidad(t)
    assert "más estrecho" in c["mensajes"]["estudio"]["XRP"] and "30 días verificados" in c["mensajes"]["estudio"]["XRP"]
    assert any("Rango desviado en XRP" in x for x in c["mensajes"]["datos"])


# ---------- frescura y cobertura: datos caducados fuera, lectura parcial ----------
def _vivo(tmp_path, *simbolos):
    sv, t = servicio_con(tmp_path, *simbolos)
    sv.order, sv.off = list(simbolos), set(SIMBOLOS) - set(simbolos)
    sv.mk = {s: {"mc": 1000.0 * (i + 1)} for i, s in enumerate(simbolos)}
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    return sv, t


def test_lectura_completa_no_muestra_avisos(tmp_path):
    sv, t = _vivo(tmp_path, "BTC", "ETH", "SOL")
    c = sv.calidad(t)
    assert c["vigentes"] == ["BTC", "ETH", "SOL"] and not c["parcial"] and c["mensajes"]["mercado"] == []
    assert c["mensajes"]["estudio"] == {} and c["cobertura"] == {"activos": [3, 3], "capitalizacion": 1.0}


def test_dato_caducado_queda_fuera_y_la_lectura_es_parcial(tmp_path):
    sv, t = _vivo(tmp_path, "BTC", "ETH", "SOL")
    sv.data["SOL"]["precio_t"] = t - 200_000
    c = sv.calidad(t)
    assert c["parcial"] and c["vigentes"] == ["BTC", "ETH"] and c["caducados"][0]["s"] == "SOL"
    assert c["mensajes"]["mercado"] == ["Lectura parcial por datos desactualizados en SOL: el resumen usa 2 de 3 activos."]
    assert c["cobertura"]["capitalizacion"] == pytest.approx(3000 / 6000)
    assert "en suspenso" in c["mensajes"]["estudio"]["SOL"] and "BTC" not in c["mensajes"]["estudio"]
    # el sentimiento ya no cuenta la lectura técnica del activo caducado
    for s in ("BTC", "ETH"):
        sv.data[s]["precio_t"] = t - 200_000
    with patch.object(servicio, "now_ms", return_value=t):
        sv.calc_sentimiento()
    assert not sv.sent or "Amplitud técnica" not in [x["nombre"] for x in sv.sent["componentes"]]


def test_derivados_caducados_no_entran_en_la_lectura(tmp_path):
    sv, t = _vivo(tmp_path, "BTC")
    sv.deriv_t = t - 45 * 60_000
    c = sv.calidad(t)
    assert not c["derivados"]["ok"] and c["parcial"]
    assert c["mensajes"]["mercado"] == ["Derivados sin renovar desde hace 45 min: no entran en la lectura."]


def test_precio_rest_fuera_de_la_horquilla_no_parece_actual(tmp_path):
    assert servicio.confirmado_rest({"c": ["100"], "a": ["100.05"], "b": ["99.95"]})
    assert not servicio.confirmado_rest({"c": ["98"], "a": ["100.05"], "b": ["99.95"]})   # último negociado viejo
    assert not servicio.confirmado_rest({"c": ["100"]})
    fx = json.loads((RAIZ / "tests" / "fixtures" / "mercado.json").read_text())
    sv, _ = servicio_con(tmp_path)
    sv.pairs, sv.tk = fx["pairs"], fx["ticker"]
    o = sv.run("ETH", now=fx["now"])                                  # en la foto real, ETH negoció fuera de la horquilla
    assert o["_precio_t"] == 0
    o["precio_t"] = 5
    sv.data = {"ETH": o}
    sv.k.get = lambda p: fx["ticker"]                                  # respaldo REST con la misma foto
    sv.live_rest()
    assert o["precio_t"] == 5                                          # no se marca como «de ahora»
    with patch.object(servicio, "now_ms", return_value=fx["now"] + 1000):
        sv.tiempo_real()
    assert o["decision"]["accion"] == "ND"


# ---------- hueco de seguimiento, reconexión y reinicio ----------
def test_hueco_de_seguimiento_se_resume_en_una_linea(tmp_path):
    sv, t = servicio_con(tmp_path, "BTC")
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    p = sv.evid.detalle("BTC", t - t % HORA)["emision"]["precio"]
    guardar(sv.evid, "BTC", velas(t, t + 3 * HORA, p), t + 3 * HORA)     # luego nada: el servicio se apagó
    sv.ciclo_evidencias(t + 2 * DIA)
    assert sv.hist_ev["activos"]["BTC"]["sin_seguimiento"] == 0       # las velas de 15 min aún pueden cubrirlo
    sv.ciclo_evidencias(t + 8 * DIA)                                  # fuera del alcance de 7,5 días
    r = sv.hist_ev["activos"]["BTC"]
    assert r["sin_seguimiento"] == 1 and r["motivo_sin_seguimiento"].startswith("Servicio apagado")
    lin = sv._mensajes_historial(sv.hist_ev, t + 8 * DIA)
    assert lin[-1] == "1 proyección sin seguimiento en los últimos 9 días (BTC): servicio apagado."


def test_evaluador_no_relee_cada_30_s_una_emision_con_hueco(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    em = emision()
    alm.emitir(em)
    guardar(alm, "BTC", velas(T0, T0 + 20 * HORA), T0 + 20 * HORA)   # hueco desde las 20 h: aún rellenable por REST
    leidas = []
    real = alm.velas
    alm.velas = lambda *a: leidas.append(a) or real(*a)
    t = T0 + DIA + 10 * MIN                                            # el hueco aún puede rellenarse: pendiente
    for k in range(10):
        assert alm.evaluar_pendientes(t + k * 30_000) == 0
    assert len(leidas) == 1
    alm.evaluar_pendientes(t + EVM.REEVALUAR + 1)
    assert len(leidas) == 2


def test_reconexion_marca_incompleto_el_minuto_cortado():
    s = "SOL/EUR"
    fl = Flujo([s], {s: (2, 8)})
    m = 1_795_000_020_000 - 1_795_000_020_000 % MIN
    fl.trade_desde[s] = m - MIN
    fl.agregados[s][m] = {"compra": 10.0, "venta": 0.0, "n": 1, "q": 1.0, "completo": True}
    fl.agregados[s][m - MIN] = {"compra": 5.0, "venta": 0.0, "n": 1, "q": 1.0, "completo": True}
    fl.ultimo = (m + 20_000) / 1000                                    # se corta a los 20 s del minuto
    fl._desconectado()
    assert fl.agregados[s][m]["completo"] is False and fl.agregados[s][m - MIN]["completo"] is True


def test_reinicio_no_duplica_y_reconstruye_la_cache(tmp_path):
    ruta = tmp_path / "e.sqlite3"
    alm = Almacen(ruta)
    for s in SIMBOLOS:
        assert alm.emitir(emision(T0, s))
    alm2 = Almacen(ruta)                                               # reinicio
    assert all(not alm2.emitir(emision(T0, s)) for s in SIMBOLOS)
    assert len(alm2.items()) == len(SIMBOLOS)
    for s in SIMBOLOS:
        guardar(alm2, s, velas(T0 - 5 * MIN, T0 + DIA + 5 * MIN), T0 + DIA + 5 * MIN)
    assert alm2.evaluar_pendientes(T0 + DIA + 5 * MIN) == len(SIMBOLOS)
    assert Almacen(ruta).evaluar_pendientes(T0 + DIA + 10 * MIN, forzar=True) == 0
    assert {x["estado"] for x in Almacen(ruta).items()} == {"verificada"}


def test_mercado_distinto_no_se_evalua_con_otras_velas(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    em = emision()
    em["mercado"]["par"] = "BTC/USD"
    alm.emitir(em)
    guardar(alm, "BTC", velas(T0 - 5 * MIN, T0 + DIA + 5 * MIN), T0 + DIA + 5 * MIN)
    alm.evaluar_pendientes(T0 + DIA + 5 * MIN)
    x = alm.items("BTC")[0]
    assert x["estado"] == "sin_seguimiento" and x["motivo"].startswith("Mercado distinto")


# ---------- avisos suspendidos ----------
def test_aviso_de_contexto_suspendido_con_datos_caducados_en_cualquier_moneda(tmp_path):
    av = Avisos(tmp_path / "e.sqlite3")
    ctx = lambda: {"precio": 1}                                         # noqa: E731
    t = T0
    av.observar("ZEC", None, {"accion": "E"}, True, t, ctx)
    emitidos = []
    for k in range(1, 400):                                            # cambia a «C» y se mantiene, pero a los 200 s caduca
        t = T0 + k * 1000
        emitidos += av.observar("ZEC", None, {"accion": "C"}, 200 > k or k > 220, t, ctx)
    assert emitidos == []                                              # la cuenta de 5 min se reinició al caducar
    for k in range(400, 560):
        t = T0 + k * 1000
        emitidos += av.observar("ZEC", None, {"accion": "C"}, True, t, ctx)
    assert len(emitidos) == 1 and av.lista()[0]["simbolo"] == "ZEC"


def test_fuentes_caducadas_suspenden_avisos_en_todas_las_monedas(tmp_path):
    sv, t = _vivo(tmp_path, "HYPE")
    sv.data["HYPE"]["precio_t"] = t - 300_000
    f = sv.estado_fuentes("HYPE", sv.data["HYPE"], t)
    assert "cruce de niveles" in f["suspendidos"] and "emisión de la proyección horaria" in f["suspendidos"]


# ---------- almacenamiento ----------
def test_rest_solo_escribe_lo_nuevo_y_un_tramo(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    filas = velas(T0, T0 + 720 * MIN)
    assert alm.guardar_rest("BTC", filas, filas[-1]["t"], T0) == len(filas)
    filas2 = velas(T0 + 10 * MIN, T0 + 730 * MIN)
    assert alm.guardar_rest("BTC", filas2, filas2[-1]["t"], T0 + 1) == 10     # antes reescribía las 720
    with sqlite3.connect(alm.ruta) as db:
        assert db.execute("SELECT count(*) FROM cobertura_1m WHERE fuente='rest'").fetchone()[0] == 1
        assert db.execute("SELECT min(desde), max(hasta) FROM cobertura_1m").fetchone() == (filas[0]["t"], filas2[-1]["t"] + MIN)


def test_compactar_velas_antiguas_conserva_el_detalle(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    alm.emitir(emision())
    guardar(alm, "BTC", velas(T0 - 5 * MIN, T0 + DIA + 5 * MIN, cambios={T0 - T0 % MIN + 60 * MIN: {"h": 106.0}}), T0 + DIA)
    alm.evaluar_pendientes(T0 + DIA + 5 * MIN)
    antes = alm.detalle("BTC", T0 - T0 % HORA)
    borradas = alm.compactar(T0 + 40 * DIA)
    despues = alm.detalle("BTC", T0 - T0 % HORA)
    assert borradas > 1400 and despues["integra"] and despues["resultado"] == antes["resultado"]
    assert max(x[1] for x in despues["serie"]) == 106.0 and len(despues["serie"]) == len(antes["serie"])
    assert alm.compactar(T0 + 40 * DIA + 1000) == 0                    # una vez al día


def test_emision_pendiente_protege_sus_velas_al_compactar(tmp_path):
    alm = Almacen(tmp_path / "e.sqlite3")
    alm.emitir(emision())
    guardar(alm, "BTC", velas(T0 - 5 * MIN, T0 + 2 * HORA), T0 + 2 * HORA)
    alm.compactar(T0 + 40 * DIA)                                       # sigue pendiente (sin resultado): no se toca
    assert len(alm.velas("BTC", T0 - 5 * MIN, T0 + 2 * HORA)) > 100


def test_registro_de_decisiones_compacto(tmp_path):
    reg = Registro(tmp_path / "d.sqlite3")
    d = {"accion": "E", "motivo": "x", "precio_t": 1000, "zona": [1.123456789012, 2.0], "caduca": 2000}
    reg.guardar("BTC", {"price": 57551.123456789, "prev": {"factores": [{"valor": -0.071190476190476}]}}, d, 1000)
    with sqlite3.connect(tmp_path / "d.sqlite3") as db:
        tx = db.execute("SELECT detalle FROM decisiones").fetchone()[0]
    assert ", " not in tx and '"precio":57551.12' in tx and "-0.07119048" in tx and json.loads(tx)["zona"][0] == 1.123457


# ---------- Datos compacto ----------
@pytest.mark.parametrize("estado, esperado", [
    ("vacio", "Historial aún insuficiente para valorar las proyecciones: aún no hay proyecciones guardadas."),
    ("pendientes", "Historial aún insuficiente para valorar las proyecciones: 8 emitidas, ninguna ha vencido todavía."),
    ("en_linea", "Proyecciones de 24 h: el rango del 80 % contuvo el precio en el 80 % de los días"),
])
def test_datos_compacto_en_los_estados_habituales(tmp_path, estado, esperado):
    alm = Almacen(tmp_path / "e.sqlite3")
    if estado == "pendientes":
        for s in SIMBOLOS:
            alm.emitir(emision(T0, s))
    if estado == "en_linea":
        for s in ("BTC", "ETH"):
            _verificadas(alm, s, 40, lambda d: d % 5 != 0, por_dia=3)
    lin = servicio.Servicio._mensajes_historial(alm.historial(T0 + 2 * HORA if estado != "en_linea" else T0 + 50 * DIA),
                                                T0 + 50 * DIA)
    assert len(lin) == 1 and lin[0].startswith(esperado)
    if estado == "en_linea":
        assert lin[0].endswith("2 activos con el mismo peso), en línea con lo indicado.")


def test_pagina_datos_no_carga_el_diagnostico_hasta_abrirlo():
    html = (RAIZ / "web" / "index.html").read_text()
    assert "async function evLoad(force){if(!DIAG||" in html               # sin diagnóstico no se pide nada técnico
    i = html.index("function viewData(){")
    cuerpo = html[i:html.index("function evBind(){")]
    compacto = cuerpo[:cuerpo.index("if(!DIAG)return;")]
    assert "Histórico profundo" not in compacto and "Comprobación del rango" not in compacto
    assert "Procedencia y estado" not in compacto and "/api/" not in compacto
    assert "avisoBreve(CALI&&CALI.mensajes.mercado)" in html                # Mercado: aviso breve solo si es parcial
    assert re.search(r"CALI&&CALI\.mensajes\.estudio\[s\]", html)            # Estudio: idem por activo


def test_servidor_sirve_el_catalogo_y_el_historial():
    """Sin importar servidor.py (crearía el servicio sobre la base real): se comprueban sus rutas en el código."""
    src = (RAIZ / "servidor.py").read_text()
    assert 'u.path == "/catalogo.js"' in src and "catalogo.web()" in src and '"/api/historial"' in src
    js = "window.CATALOGO=" + json.dumps(catalogo.web(), ensure_ascii=False, separators=(",", ":")) + ";"
    assert all(f'"{s}"' in js for s in SIMBOLOS)
