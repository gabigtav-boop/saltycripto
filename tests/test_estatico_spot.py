"""Regresión del «Pendiente de datos» de la web publicada (20 nov 2026).

La foto de GitHub Actions llega cada ~50-60 min, así que su `precio_t` siempre tiene más de 120 s. La decisión spot de la web
publicada la recalcula el navegador (`decisionEstatica`) y pone en `decision.precio_t` la hora en que el WebSocket confirma el
precio; `lib/spot.js` leía en cambio el `precio_t` de la foto, y todos los activos salían «Pendiente de datos» aunque los
precios estuvieran en vivo. Esta prueba ejecuta las funciones reales de la página (`vistaSpot`, `datosSpot`, `lecturaSpot`)
con `lib/spot.js` y comprueba los casos reciente, caducado, parcial, hora futura y fallo de carga."""
import json
import pathlib
import re
import subprocess

RAIZ = pathlib.Path(__file__).resolve().parents[1]
HTML = (RAIZ / "web" / "index.html").read_text()
SPOT = (RAIZ / "web" / "lib" / "spot.js").read_text()


def _funcion(nombre):
    m = re.search(r"function " + nombre + r"\(.*?\n", HTML)
    assert m, nombre
    return m.group(0)


def test_la_web_publicada_usa_la_hora_confirmada_en_vivo():
    js = "const window={ESTATICO:true};globalThis.window=window;\n" + SPOT + "\n" + \
        "".join(_funcion(f) for f in ("vistaSpot", "datosSpot", "lecturaSpot")) + r"""
const COINS={BTC:{},ETH:{},SUI:{},PAXG:{cr:false}};
const ahora=Date.now(), foto=ahora-50*60*1000;                     // foto de hace 50 min, como en GitHub Pages
const dec=(accion,precio_t)=>({accion,motivo:'m',zona:null,cobertura:.9,precio_t,caduca:ahora+5000});
let DATA={
  BTC:{price:1,precio_t:foto,decision:dec('V',ahora)},             // WebSocket confirma ahora
  ETH:{price:1,precio_t:foto,decision:dec('C',ahora-1000)},        // confirmado hace 1 s
  SUI:{price:1,precio_t:foto,decision:dec('ND',0)},                // sin ticker en esta conexión: caducado
  PAXG:{price:1,precio_t:foto,decision:dec('E',ahora)}};
const r={};
r.btc=lecturaSpot(DATA.BTC);r.eth=lecturaSpot(DATA.ETH);r.sui=lecturaSpot(DATA.SUI);
r.foto_intacta=DATA.BTC.precio_t===foto;                            // la vista no modifica el dato de la foto
r.resumen=SPOT.resumen(['BTC','ETH','SUI','PAXG'],datosSpot(),COINS,()=>false,ahora).cobertura;
// la regla de 120 s se mantiene: una confirmación de hace 121 s no vale, una de hace 120 s sí
r.c121=SPOT.lectura(vistaSpot({price:1,precio_t:foto,decision:Object.assign(dec('V',ahora-121000),{caduca:ahora+5000})}),ahora).accion;
r.c120=SPOT.lectura(vistaSpot({price:1,precio_t:foto,decision:Object.assign(dec('V',ahora-120000),{caduca:ahora+5000})}),ahora).accion;
// hora futura: nunca apta
r.futura=SPOT.lectura(vistaSpot({price:1,precio_t:foto,decision:dec('C',ahora+60000)}),ahora).estado;
// fallo de carga: sin datos del activo
r.sin=lecturaSpot(undefined).estado;
DATA={};r.vacio=SPOT.resumen(['BTC','ETH'],datosSpot(),COINS,()=>false,ahora).cobertura;
// en el Mac (sin ESTATICO) la vista no cambia nada
window.ESTATICO=false;r.mac=lecturaSpot({price:1,precio_t:foto,decision:dec('V',ahora)}).accion;
console.log(JSON.stringify(r));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["btc"]["accion"] == "V" and r["btc"]["apto"]
    assert r["eth"]["accion"] == "C" and r["eth"]["apto"]
    assert r["sui"]["accion"] == "ND"
    assert r["foto_intacta"]
    assert r["resumen"] == [2, 3]                                       # parcial: 2 de 3 cripto evaluados
    assert r["c121"] == "ND" and r["c120"] == "V"
    assert r["futura"] == "futura"
    assert r["sin"] == "sin_dato" and r["vacio"] == [0, 2]
    assert r["mac"] == "ND"                                             # sin modo estático manda el precio_t del dato


def test_todas_las_lecturas_pasan_por_la_vista():
    assert "SPOT.resumen(coins(),DATA," not in HTML and "SPOT.resumen(L,DATA," not in HTML
    assert HTML.count("datosSpot()") >= 2
    assert re.search(r"function lecturaSpot\(o\)\{return SPOT\.lectura\(vistaSpot\(o\)", HTML)
