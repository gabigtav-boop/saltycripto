"""Evidencias en la versión nube: nube.py publica como archivos lo que en el Mac sirve la API, y la página los lee."""
import json
import subprocess
from unittest.mock import patch

import nube
from cripto import servicio
from cripto.evidencias import DIA, HORA, MIN
from test_evidencias import RAIZ, guardar, servicio_con, velas


def test_publica_historial_resumen_minutos_y_detalle(tmp_path, monkeypatch):
    sv, t = servicio_con(tmp_path, "BTC", "ETH")
    sv.avisos = None                                   # como en nube.py
    with patch.object(servicio, "now_ms", return_value=t):
        sv.tiempo_real()
    hora = t - t % HORA
    p0 = sv.evid.detalle("BTC", hora)["emision"]["precio"]
    fin = t + DIA + 5 * MIN
    guardar(sv.evid, "BTC", velas(t - 5 * MIN, fin, p0), fin)
    sv.net = False                                     # sin red: no pide velas REST
    monkeypatch.setattr(nube, "SALIDA", tmp_path / "publico")
    monkeypatch.setattr(nube.time, "time", lambda: fin / 1000)
    ahora = nube.evidencias(sv)
    assert ahora == fin and sv._calidad and "mensajes" in sv._calidad
    assert nube.publicar_evidencias(sv, ahora) > 0
    base = tmp_path / "publico" / "datos" / "evidencias"
    h = json.loads((base / "historial.json").read_text())
    assert set(h["activos"]) >= {"BTC", "ETH"}
    r = json.loads((base / "BTC.json").read_text())
    assert r["cuenta"]["verificadas"] == 1 and r["lista"][0]["hora"] == hora
    d = json.loads((base / "BTC" / f"{hora}.json").read_text())
    assert d["estado"] == "verificada" and d["integra"]
    m = json.loads((base / "BTC_min.json").read_text())
    assert m["s"] == "BTC" and len(m["minutos"]) == 180


def test_omision_de_la_hora_se_borra_si_otra_ejecucion_emite(tmp_path):
    sv, t = servicio_con(tmp_path, "BTC")
    hora = t - t % HORA
    sv.evid.omision("BTC", hora, "precio no vigente", t)        # una ejecución anterior de esta hora no pudo emitir
    with patch.object(servicio, "now_ms", return_value=t + 60_000):
        sv.tiempo_real()                                         # esta sí
    with sv.evid._db() as db:
        assert not db.execute("SELECT 1 FROM omisiones_proy WHERE simbolo='BTC' AND hora=?", (hora,)).fetchone()
        assert db.execute("SELECT 1 FROM proyecciones WHERE simbolo='BTC' AND hora=?", (hora,)).fetchone()


def test_pagina_publicada_lee_archivos_estaticos():
    html = (RAIZ / "web" / "index.html").read_text()
    a = html.index("const evURL=")
    js = html[a:html.index("\n", html.index("const jget=", a))]
    prueba = js + """
const r=[evURL('/api/historial'),evURL('/api/evidencias?s=BTC'),evURL('/api/minutos?s=ETH&n=180'),evURL('/api/evidencia?s=SOL&h=123'),evURL('/api/avisos?s=BTC'),evURL('/api/spot')];
console.log(JSON.stringify(r));"""
    out = subprocess.run(["node", "-e", "var window={ESTATICO:true};" + prueba], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == ["datos/evidencias/historial.json", "datos/evidencias/BTC.json", "datos/evidencias/ETH_min.json",
                               "datos/evidencias/SOL/123.json", None, None]
    # en local (con servidor) las rutas no cambian
    out = subprocess.run(["node", "-e", "var window={};" + prueba], capture_output=True, text=True, check=True).stdout
    assert json.loads(out)[0] == "/api/historial"
    assert "function calidadEstatica(" in html and "CALI=calidadEstatica();" in html
    assert "filter(t=>!(window.ESTATICO&&t[0]==='avi'))" in html          # sin servidor no hay avisos con registro
    src = (RAIZ / "nube.py").read_text()
    assert 'sv.avisos = None' in src and 'sv.live_rest()' in src and '"catalogo.js"' in src
