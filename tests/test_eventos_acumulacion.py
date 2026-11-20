"""Eventos clave, condiciones de acumulación en caídas y resumen de Datos (19-20 nov 2026)."""
import datetime as dt
import json
import math
import pathlib
import sqlite3

import pytest

from cripto import acumulacion as A
from cripto import eventos as E
from cripto import noticias
from cripto.decision import Registro
from cripto.servicio import Servicio

RAIZ = pathlib.Path(__file__).resolve().parents[1]
HORA, DIA = 3_600_000, 86_400_000


def ms(*a):
    return int(dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp() * 1000)


# ---------- eventos clave ----------
def test_validar_limpia_y_rechaza():
    e = E.validar({"titulo": "  Salida   a bolsa de X ", "tipo": "raro", "certeza": "seguro", "activos": ["BTC", "NOPE"],
                   "fuente": "javascript:alert(1)", "fecha": ms(2027, 3, 1)})
    assert e["titulo"] == "Salida a bolsa de X" and e["tipo"] == "otro" and e["certeza"] == "rumor"
    assert e["activos"] == ["BTC"] and e["fuente"] == "" and e["impacto"] == "medio" and e["id"]
    for malo in ({"titulo": "x"}, {"titulo": "Evento", "fecha": 10}, {"titulo": "Evento", "fecha": float("nan")},
                 {"titulo": "Evento", "fecha": True}, "no es un dict"):
        with pytest.raises(ValueError):
            E.validar(malo)


def test_lista_en_disco_anadir_quitar_descartar_y_edicion_a_mano(tmp_path):
    ruta = tmp_path / "eventos.json"
    ev = E.Eventos(ruta)
    assert ev.items == [] and ev.error is None
    i = ev.anadir({"titulo": "Decisión del ETF de SOL", "tipo": "etf", "fecha": ms(2026, 12, 1)})
    with pytest.raises(ValueError):
        ev.anadir({"id": i, "titulo": "Otro con el mismo id"})
    ev.descartar("clave-noticia")
    d = json.loads(ruta.read_text())
    assert [x["id"] for x in d["eventos"]] == [i] and d["descartados"] == ["clave-noticia"]
    assert not list(tmp_path.glob("*.tmp"))                              # escritura atómica
    # edición a mano: se relee al cambiar el archivo y se ignoran (contándolos) los eventos no válidos
    d["eventos"].append({"titulo": "x"})
    d["eventos"].append({"titulo": "Actualización de red", "tipo": "red"})
    ruta.write_text(json.dumps(d))
    assert ev.recargar() and len(ev.items) == 2 and "1 eventos no válidos" in ev.error
    assert ev.quitar(i) and not ev.quitar(i) and len(E.Eventos(ruta).items) == 1
    ruta.write_text("{roto")
    ev2 = E.Eventos(ruta)
    assert ev2.items == [] and "no se pudo leer" in ev2.error


def test_hoy_es_el_dia_de_canarias():
    # 23:30 UTC del 10 de julio = 00:30 del 11 en Canarias (verano, UTC+1)
    e = {"fecha": ms(2026, 7, 10, 23, 30)}
    assert E.estado(e, ms(2026, 7, 11, 9, 0)) == "hoy"
    assert E.estado(e, ms(2026, 7, 10, 22, 0)) == "previsto"
    assert E.estado(e, ms(2026, 7, 12, 9, 0)) == "pasado"
    assert E.estado({"fecha": None}, ms(2026, 7, 12)) == "sin_fecha"
    # invierno: Canarias = UTC
    assert E.estado({"fecha": ms(2026, 11, 19, 23, 30)}, ms(2026, 11, 20, 0, 10)) == "pasado"


def test_reaccion_usa_la_apertura_y_compara_con_lo_habitual():
    t0 = ms(2026, 6, 1)
    import random
    rnd, p, rows = random.Random(3), 100.0, []
    for i in range(120 * 24):                          # paseo aleatorio: hay volatilidad habitual con la que comparar
        c = p * math.exp(rnd.gauss(0, .005))
        rows.append({"t": t0 + i * HORA, "o": p, "h": max(p, c), "l": min(p, c), "c": c, "v": 1.0})
        p = c
    f = t0 + 100 * DIA + 30 * 60000                    # a mitad de la vela: se usa su apertura, no su cierre
    i0 = (f - t0) // HORA
    ev = {"fecha": f, "activos": ["MERCADO"]}
    r = E.reaccion(ev, {"BTC": rows}, f + 2 * HORA, {"BTC": rows[i0]["o"] * 1.05})
    assert r["BTC"]["p0"] == rows[i0]["o"] and r["BTC"]["desde"]["pct"] == pytest.approx(5)
    assert "24h" not in r["BTC"]                       # aún no se ha cumplido el plazo
    r = E.reaccion(ev, {"BTC": rows}, f + 9 * DIA)
    assert set(r["BTC"]) >= {"24h", "7d"} and "desde" not in r["BTC"]
    assert r["BTC"]["24h"]["lectura"] in ("dentro de lo habitual", "movimiento amplio", "movimiento inusual")
    assert E.reaccion({"fecha": None, "activos": ["BTC"]}, {"BTC": rows}, f) is None
    assert E.reaccion({"fecha": f + DIA, "activos": ["BTC"]}, {"BTC": rows}, f) is None      # aún no ha pasado


def test_publico_ordena_y_oculta_lo_antiguo():
    ahora = ms(2026, 11, 20, 12)
    its = [E.validar({"id": i, "titulo": "Evento " + i, "fecha": f}) for i, f in
           (("viejo", ahora - 20 * DIA), ("pasado", ahora - 2 * DIA), ("sinf", None), ("prox", ahora + 3 * DIA),
            ("hoy", ahora - HORA))]
    assert [x["id"] for x in E.publico(its, ahora)] == ["hoy", "prox", "sinf", "pasado"]


def test_candidatos_no_repiten_eventos_y_proponen_actualizaciones():
    ahora = ms(2026, 11, 20, 12)
    its = [E.validar({"id": "ant", "titulo": "Anthropic sale a bolsa en el Nasdaq (ANT)", "tipo": "salida_bolsa",
                      "fecha": ahora - DIA}),
           E.validar({"id": "oai", "titulo": "Salida a bolsa de OpenAI", "tipo": "salida_bolsa"})]
    def n(t, temas=("Salidas a bolsa",), cob=2, fuerza=2.0, edad=HORA):
        return {"t": ahora - edad, "titulo": t, "temas": list(temas), "cobertura": cob, "fuerza": fuerza, "afecta": {"MERCADO": 0}}
    L = [n("Dow falls 1,000 points as Anthropic IPO dominates"), n("OpenAI sets IPO date for March"),
         n("SpaceX files for IPO on Nasdaq"), n("Tiny company IPO", cob=1, fuerza=.8),
         n("SEC approves spot SOL ETF", ("Regulación", "ETF e institucionales"), cob=3, fuerza=7),
         n("Old IPO news", edad=4 * DIA)]
    c = E.candidatos(L, its, [], ahora)
    assert [x["titulo"] for x in c] == ["OpenAI sets IPO date for March", "SpaceX files for IPO on Nasdaq",
                                        "SEC approves spot SOL ETF"]
    assert c[0]["actualiza"]["id"] == "oai" and c[1]["actualiza"] is None and c[2]["tipo"] == "etf"
    assert [x["titulo"] for x in E.candidatos(L, its, [c[1]["clave"]], ahora)][1] == "SEC approves spot SOL ETF"


def test_noticias_de_salidas_a_bolsa_tienen_tema_propio():
    a = noticias.analizar({"titulo": "Anthropic stock pops 30% in mega IPO on Nasdaq", "desc": ""}, "macro", 1.0)
    assert "Salidas a bolsa" in a["temas"] and a["afecta"].get("MERCADO", 0) > 0
    b = noticias.analizar({"titulo": "Startup postpones IPO amid slump", "desc": ""}, "macro", 1.0)
    assert "Salidas a bolsa" in b["temas"] and b["afecta"]["MERCADO"] < 0
    assert "Salidas a bolsa" in noticias.HORIZ


def test_servicio_publica_eventos_en_el_estado(tmp_path):
    ruta = tmp_path / "eventos.json"
    ev = E.Eventos(ruta)
    ev.anadir({"titulo": "Salida a bolsa de OpenAI", "tipo": "salida_bolsa"})
    sv = Servicio(net=False, eventos=ev)
    sv.recalc_eventos()
    st = json.loads(sv.snapshot())
    assert st["eventos"]["lista"][0]["estado"] == "sin_fecha" and st["eventos"]["candidatos"] == []


def test_api_de_eventos(tmp_path, monkeypatch):
    import http.client
    import threading

    import servidor

    monkeypatch.setattr(servidor.SV, "eventos", E.Eventos(tmp_path / "ev.json"))
    srv = servidor.Servidor(("127.0.0.1", 0), servidor.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def pide(metodo, ruta, cuerpo=None, ctype="application/json"):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request(metodo, ruta, body=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                      headers={"Host": "localhost", **({"Content-Type": ctype} if cuerpo is not None else {})})
            r = c.getresponse()
            b = r.read()
            c.close()
            return r.status, json.loads(b or b"{}")
        st, b = pide("POST", "/api/eventos", {"accion": "anadir", "evento": {"titulo": "Decisión del ETF de SUI", "tipo": "etf"}})
        assert st == 200 and b["ok"]
        st, b = pide("GET", "/api/eventos")
        assert st == 200 and b["lista"][0]["titulo"] == "Decisión del ETF de SUI" and "salida_bolsa" in b["tipos"]
        assert pide("POST", "/api/eventos", {"accion": "anadir", "evento": {"titulo": "x"}})[0] == 400
        assert pide("POST", "/api/eventos", {"accion": "otra"})[0] == 400
        assert pide("POST", "/api/eventos", {"accion": "quitar", "id": b["lista"][0]["id"]}, "text/plain")[0] == 403
        assert pide("POST", "/api/eventos", {"accion": "quitar", "id": b["lista"][0]["id"]})[0] == 200
        assert pide("GET", "/api/eventos")[1]["lista"] == []
    finally:
        srv.shutdown()
        srv.server_close()


# ---------- acumulación en caídas ----------
def _diarias(precios, vol=None):
    t0 = ms(2025, 1, 1)
    out, prev = [], precios[0]
    for i, c in enumerate(precios):
        o = prev
        out.append({"t": t0 + i * DIA, "o": o, "h": max(o, c) * 1.01, "l": min(o, c) * .99, "c": c,
                    "v": (vol[i] if vol else 100.0)})
        prev = c
    return out


def test_condiciones_en_un_desplome_y_en_una_subida():
    sube = [100 * 1.004 ** i for i in range(200)]
    baja = sube + [sube[-1] * .97 ** i for i in range(1, 16)]
    vol = [100.0] * len(baja)
    vol[-2] = 400.0                                                  # volumen de capitulación en una vela bajista
    S = A.series(_diarias(baja, vol))
    c = A.condiciones(S, len(baja) - 1, fng=12, funding3d=-1e-5)
    assert c == {"caida": True, "rsi": True, "bollinger": c["bollinger"], "capitulacion": True, "miedo": True, "funding": True}
    sg = A.senal(c)
    assert sg["activa"] and sg["disponibles"] == 5
    S2 = A.series(_diarias(sube))
    c2 = A.condiciones(S2, len(sube) - 1, fng=None, funding3d=None)
    assert c2["caida"] is False and c2["miedo"] is None and c2["funding"] is None
    assert not A.senal(c2)["activa"] and A.senal(c2)["disponibles"] == 3
    # la caída sola no basta
    assert not A.senal({"caida": True, "rsi": False, "bollinger": False, "capitulacion": False, "miedo": None,
                        "funding": None})["activa"]
    assert A.condiciones(S2, len(sube) - 1, funding3d=float("nan"))["funding"] is None


def test_el_texto_depende_del_veredicto_del_estudio():
    sube = [100 * 1.004 ** i for i in range(200)]
    baja = sube + [sube[-1] * .97 ** i for i in range(1, 16)]
    rows = _diarias(baja)
    z = [{"mid": baja[-1] * .95, "lo": 1, "hi": 2}, {"mid": baja[-1] * .9, "lo": 1, "hi": 2}, {"mid": baja[-1] * .8, "proj": True}]
    a = A.lectura_viva(rows, 10, -1e-5, z, baja[-1], {"veredicto": "no demostrada"})
    assert a["estado"] == "capitulacion" and "no demostrada" in a["texto"]
    assert A.lectura_viva(rows, 10, -1e-5, z, baja[-1], {"veredicto": "demostrada"})["estado"] == "zona"
    t = a["tramos"]
    assert t["niveles"][0] > t["niveles"][1] > t["niveles"][2] > t["revisar"]
    assert t["niveles"][1] == pytest.approx(baja[-1] * .95)        # la zona proyectada no se usa
    assert A.lectura_viva(rows[:50], 10, None, z, 1, None) is None
    assert A.lectura_viva(_diarias(sube), None, None, z, sube[-1], None)["estado"] == "no"


def test_tramos_sin_soportes_y_soportes_mal_ordenados():
    t = A.tramos(100, [], 3)
    assert t["niveles"] == [100, 97, 92.5] and t["revisar"] == 89.5
    t = A.tramos(100, [{"mid": 99}, {"mid": 99.5}], 3)
    assert t["niveles"][0] > t["niveles"][1] > t["niveles"][2]


def test_servicio_usa_velas_cerradas_y_excluye_el_oro():
    sv = Servicio(net=False)
    sube = [100 * 1.004 ** i for i in range(200)]
    baja = sube + [sube[-1] * .97 ** i for i in range(1, 16)]
    rows = _diarias(baja)
    ahora = rows[-1]["t"] + 2 * HORA                      # la última vela aún no ha cerrado
    o = {"raw1d": rows, "price": baja[-1], "z": {"sup": []}}
    a = sv.acumulacion("BTC", o, ahora)
    assert a["t_vela"] == rows[-2]["t"] and a["historico"]["veredicto"] in ("demostrada", "no demostrada", None)
    assert sv.acumulacion("PAXG", o, ahora) is None
    assert sv.acumulacion("BTC", {"raw1d": rows[:60], "price": 1}, ahora) is None


def test_funding_de_tres_dias_necesita_seis_fotos():
    sv = Servicio(net=False)
    ahora = ms(2026, 11, 20)
    sv._fund_hist = {"BTC": [(ahora - h * HORA, -1e-5) for h in range(5)]}
    assert sv.funding_3d("BTC", ahora) is None
    sv._fund_hist["BTC"].append((ahora - 6 * HORA, -1e-5))
    assert sv.funding_3d("BTC", ahora) == pytest.approx(-1e-5)
    sv._fund_hist["BTC"] = [(ahora - 80 * HORA, 1.0)] * 10        # fuera de las 72 h
    assert sv.funding_3d("BTC", ahora) is None


def test_registro_de_acumulacion_una_vez_por_dia_y_estado(tmp_path):
    reg = Registro(tmp_path / "d.sqlite3")
    a = {"version": "acum-1", "estado": "capitulacion", "condiciones": [{"k": "caida", "ok": True}], "cumple": 3, "total": 6,
         "dd90": -.25, "rsi": 24.0, "t_vela": 1}
    t = ms(2026, 11, 20, 10)
    reg.guardar_acum("BTC", a, 100.0, t)
    reg.guardar_acum("BTC", a, 101.0, t + HORA)                              # mismo día y estado
    reg.guardar_acum("BTC", dict(a, estado="no"), 102.0, t + 2 * HORA)       # cambia el estado
    reg.guardar_acum("BTC", a, 103.0, t + DIA)                               # día nuevo
    with sqlite3.connect(tmp_path / "d.sqlite3") as db:
        filas = db.execute("SELECT estado, precio FROM acumulacion ORDER BY t").fetchall()
    assert filas == [("capitulacion", 100.0), ("no", 102.0), ("capitulacion", 103.0)]


def test_estudio_y_vivo_usan_la_misma_senal():
    # el script del estudio 11 vive en la versión del Mac; aquí solo viaja su resultado (resultados_estudio11.json)
    r = json.loads((RAIZ / "estudios" / "resultados_estudio11.json").read_text())
    assert r["version"] == A.VERSION and r["veredicto"] in ("demostrada", "no demostrada")
    assert math.isfinite(r["conjunto"]["catalogo"]["ventaja_media"])


# ---------- web ----------
def test_pagina_resume_datos_y_muestra_eventos_y_acumulacion():
    html = (RAIZ / "web" / "index.html").read_text()
    for x in ("function resumenDatos", "¿Me puedo fiar ahora?", "¿Acierta el sistema?", "function evCard", "/api/eventos",
              "Evento clave · hoy", "function acuCard", "ventaja no demostrada", "Plan escalonado · no validado",
              "editandoEv()"):
        assert x in html, x
    # el diagnóstico técnico solo se monta al abrirlo
    i = html.index("function viewData()")
    assert "if(!DIAG)return;" in html[i:i + 2500]
