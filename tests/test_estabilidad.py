"""Versión de estabilidad (20 nov 2026), traída del Mac: bloques A, B, D y E. C (evaluación spot) y F (lanzador) son solo del
Mac. Aquí se añade la paridad de web/lib/costes.js con cripto/costes.py (la web publicada calcula el coste en el navegador).
Datos SINTÉTICOS."""
import json
import math
import pathlib
import subprocess

import pytest

from cripto.decision import FRESCO_MS, decision

RAIZ = pathlib.Path(__file__).resolve().parents[1]
T0 = 1_800_000_000_000


def activo(sem="C", dire="lateral", cob=1.0, t=T0, obsoleta=False):
    return {"precio_t": t, "tm": {"v": sem}, "prev": {"dir": dire, "cobertura": cob, "obsoleta": obsoleta},
            "z": {"sup": [{"lo": 9, "hi": 10}]}}


# ---------- A · decisión del motor ----------
@pytest.mark.parametrize("edad,accion", [(119_000, "C"), (120_000, "C"), (121_000, "ND")])
def test_frescura_119_120_121(edad, accion):
    assert decision(activo(), T0 + edad)["accion"] == accion


def test_hora_futura_no_vale():
    d = decision(activo(t=T0 + 1), T0)
    assert d["accion"] == "ND" and "posterior" in d["motivo"]


@pytest.mark.parametrize("cob,accion", [(.49, "ND"), (.50, "C")])
def test_cobertura_minima(cob, accion):
    d = decision(activo(cob=cob), T0 + 1000)
    assert d["accion"] == accion
    if accion == "ND":
        assert "49 %" in d["revisar"]


def test_calculo_obsoleto_es_nd_y_conserva_la_anterior():
    vigente = decision(activo("E"), T0 + 1000)
    assert vigente["accion"] == "E" and vigente["revisar"]
    d = decision(activo("E", obsoleta=True), T0 + 2000, vigente)
    assert d["accion"] == "ND" and d["anterior"]["accion"] == "E"
    # la anterior se mantiene mientras siga sin poder evaluarse, sin convertirse en vigente
    d2 = decision(activo("E"), T0 + FRESCO_MS + 5000, d)
    assert d2["accion"] == "ND" and d2["anterior"]["accion"] == "E"


@pytest.mark.parametrize("sem,dire,accion", [("C", "alcista", "C"), ("C", "bajista", "E"), ("V", "bajista", "V"),
                                             ("V", "alcista", "E"), ("E", "lateral", "E")])
def test_cada_accion_dice_que_la_haria_cambiar(sem, dire, accion):
    d = decision(activo(sem, dire), T0 + 1000)
    assert d["accion"] == accion and d["revisar"]


def test_node_bloques_a_b():
    r = subprocess.run(["node", str(RAIZ / "tests" / "estabilidad.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]


# ---------- D · coste para 25, 50 y 100 € ----------
from cripto import costes  # noqa: E402

LIB = {"asks": [(100.0, .1), (100.5, .2), (101.0, 1.0)], "bids": [(99.9, .1), (99.5, 1.0)], "t": T0, "completo": True}
REG = {"ordermin": .0001, "costmin": .5, "lot_decimals": 8}


def test_d_comision_de_25_euros_y_equilibrio():
    r = costes.estimar(25, "compra", {"asks": [(100.0, 10)], "bids": [(99.99, 10)], "t": T0}, T0 + 1000, REG, .004, .008)
    assert r["estado"] == "ejecutable" and r["comision"] == pytest.approx(.20) and r["total"] == pytest.approx(25.20)
    assert r["tipo_comision"] == "taker"
    assert costes.equilibrio(.008, .008) == pytest.approx((1 + .008) / (1 - .008) - 1) == pytest.approx(.016129, abs=1e-6)


def test_d_varios_niveles_sin_sumar_dos_veces_la_horquilla():
    r = costes.estimar(25, "compra", LIB, T0 + 1000, REG, 0, .008)
    # 0,1 a 100 = 10 €; 15 € a 100,5 → 0,1492537… monedas
    q = .1 + 15 / 100.5
    assert r["cantidad"] == pytest.approx(math.floor(q * 1e8) / 1e8) and r["niveles_usados"] == 2
    medio = (100 + 99.9) / 2
    assert r["desplazamiento"] == pytest.approx((r["precio_medio"] - medio) * r["cantidad"])   # ya incluye la media horquilla
    assert r["coste_total"] == pytest.approx(r["comision"] + r["desplazamiento"])


def test_d_un_nivel_falta_de_profundidad_caducado_minimo_y_redondeo():
    un = costes.estimar(50, "compra", {"asks": [(100.0, .2)], "bids": [(99.9, 1)], "t": T0}, T0, REG)
    assert un["estado"] == "insuficiente"                                    # 0,2 × 100 = 20 € < 50 €
    assert costes.estimar(100, "venta", LIB, T0, REG)["estado"] == "ejecutable"
    assert costes.estimar(25, "compra", LIB, T0 + 121_000, REG)["estado"] == "caducado"
    assert costes.estimar(25, "compra", LIB, T0 - 5, REG)["estado"] == "caducado"            # captura en el futuro
    assert costes.estimar(25, "compra", LIB, T0, {"ordermin": 1.0, "lot_decimals": 8})["estado"] == "bajo_minimo"
    assert costes.estimar(.3, "compra", LIB, T0, REG)["estado"] == "bajo_minimo"            # costmin 0,5 €
    r = costes.estimar(25, "compra", LIB, T0, {"lot_decimals": 2})
    assert r["cantidad"] == .24 and r["nominal"] <= 25                                     # redondeo hacia abajo al incremento


def test_d_solo_mejor_bid_ask_es_parcial_y_limite_que_cruza_paga_taker():
    tk = {"asks": [(100.0, float("inf"))], "bids": [(99.0, float("inf"))], "t": T0, "completo": False}
    r = costes.estimar(25, "compra", tk, T0, REG, .004, .008)
    assert r["estado"] == "parcial" and "falta la profundidad" in r["motivo"]
    cruza = costes.estimar(25, "compra", LIB, T0, REG, .004, .008, tipo="limite", limite=100.2)
    pasiva = costes.estimar(25, "compra", LIB, T0, REG, .004, .008, tipo="limite", limite=99.0)
    assert cruza["tipo_comision"] == "taker" and pasiva["tipo_comision"] == "maker" and pasiva["estado"] == "pasiva"
    assert pasiva["desplazamiento"] == 0
    assert [f["importe"] for f in costes.tabla("compra", LIB, T0)["filas"]] == [25, 50, 100]


# ---------- E · acumulación: la regla histórica acum-1, descrita literalmente ----------
from cripto import acumulacion as AC  # noqa: E402


def test_e_regla_literal_de_seis_atr():
    M, C, A = 100.0, 70.0, 4.0
    S = {"c": [C], "atr": [A], "dd": [C / M - 1], "rsi": [None], "bb": [None], "cap": [False]}
    assert AC.condiciones(S, 0)["caida"] is False                       # acum-1: 1 − C/M ≥ 6·A/C → 0,30 ≥ 0,343
    assert (M - C >= 6 * A) is True                                      # la regla verbal habría dado verdadero
    assert AC.VERSION == "acum-1" and "1 − C/M ≥ 6·ATR/C" in AC.REGLA_CAIDA
    assert "1 − C/M  ≥  K_ATR · A / C" in AC.__doc__
    html = (RAIZ / "web" / "index.html").read_text()
    assert "Capitulación técnica · ventaja no demostrada" in (RAIZ / "cripto" / "acumulacion.py").read_text()
    assert "regla_caida" in html


# ---------- D en la web publicada: web/lib/costes.js = cripto/costes.py ----------
CASOS = [
    (25, "compra", LIB, T0 + 1000, REG, .004, .008, "mercado", None),
    (50, "compra", LIB, T0 + 1000, REG, 0, .008, "mercado", None),
    (100, "venta", LIB, T0, REG, .004, .008, "mercado", None),
    (100, "compra", LIB, T0, REG, .004, .008, "mercado", None),
    (25, "compra", LIB, T0 + 121_000, REG, .004, .008, "mercado", None),
    (25, "compra", LIB, T0 - 5, REG, .004, .008, "mercado", None),
    (25, "compra", LIB, T0, {"ordermin": 1.0, "lot_decimals": 8}, .004, .008, "mercado", None),
    (.3, "compra", LIB, T0, REG, .004, .008, "mercado", None),
    (25, "compra", LIB, T0, {"lot_decimals": 2}, .004, .008, "mercado", None),
    (25, "compra", LIB, T0, REG, .004, .008, "limite", 100.2),
    (25, "compra", LIB, T0, REG, .004, .008, "limite", 99.0),
    (25, "venta", LIB, T0, REG, .004, .008, "limite", 100.7),
    (25, "compra", None, T0, REG, .004, .008, "mercado", None),
]
CAMPOS = ("estado", "cantidad", "precio_medio", "nominal", "comision", "desplazamiento", "niveles_usados", "coste_total",
          "coste_pct", "total", "tipo_comision", "tasa", "cruza", "equilibrio_taker", "equilibrio_maker_taker", "medio")


def test_d_costes_js_igual_que_python():
    tk = {"asks": [(100.0, 1e18)], "bids": [(99.0, 1e18)], "t": T0, "completo": False}
    casos = CASOS + [(25, "compra", tk, T0, REG, .004, .008, "mercado", None)]
    js = """const fs=require('fs');const vm=require('vm');const c=vm.createContext({});vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const casos=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(casos.map(a=>c.COSTES.estimar(...a))))"""
    r = subprocess.run(["node", "-e", js, str(RAIZ / "web" / "lib" / "costes.js")], input=json.dumps(casos),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    for caso, j in zip(casos, json.loads(r.stdout)):
        p = costes.estimar(*caso)
        for k in CAMPOS:
            a, b = p.get(k), j.get(k)
            if isinstance(a, float) or isinstance(b, float):
                assert a == pytest.approx(b, rel=1e-12, abs=1e-12), (caso, k, a, b)
            else:
                assert a == b, (caso, k, a, b)
