"""Previsión combinada: datos ausentes, NaN/infinitos, cobertura, pesos y separación intensidad / acuerdo / probabilidad
(auditoría 17 nov 2026). Datos sintéticos deterministas."""
import json
import math

import pytest

from cripto.prevision import PESOS, UMBRAL_COBERTURA_BAJA, factores, prevision, validar_pesos

Z = {"sup": [{"lo": 95, "hi": 97, "mid": 96, "s": 3}], "res": [{"lo": 104, "hi": 106, "mid": 105, "s": 3}]}
NAN, INF = float("nan"), float("inf")
DERIV_BAJISTA = {"ok": True, "funding": .0005, "largos_pct": 80}
DERIV_ALCISTA = {"ok": True, "funding": -.0002, "largos_pct": 40}


def moneda(ts=50, tl=50, tm="E", micro=0):
    return {"price": 100, "z": Z, "ts": ts, "tl": tl, "tm": {"v": tm}, "micro": {"score": micro, "lectura": "x"}, "d1": {"atr": 2}}


def completa(sube=True):
    if sube:
        return prevision(moneda(80, 75, "C", 60), deriv=DERIV_ALCISTA, pulso={"score": 35, "lectura": "Positivo", "n": 4},
                         sent={"valor": 25, "lectura": "Miedo"})
    return prevision(moneda(20, 30, "V", -40), deriv=DERIV_BAJISTA, pulso={"score": -40, "lectura": "Negativo", "n": 6},
                     sent={"valor": 80, "lectura": "Codicia"})


# ---------- pesos ----------
def test_pesos_heuristicos_conservados_y_suman_uno():
    assert PESOS == {"Técnico corto plazo": .22, "Técnico largo plazo": .22, "Semáforo de momento": .10, "Flujo de órdenes": .10,
                     "Noticias 24 h": .16, "Derivados (contrario)": .10, "Sentimiento (contrario)": .10}
    assert abs(sum(PESOS.values()) - 1) < 1e-12


@pytest.mark.parametrize("malos", [{"Técnico corto plazo": -.1}, {"Técnico corto plazo": NAN}, {"Técnico corto plazo": INF},
                                    {"Inventado": .5}, {"Técnico corto plazo": "mucho"}, {}, [], {k: 0 for k in PESOS}])
def test_pesos_invalidos_se_rechazan(malos):
    with pytest.raises(ValueError):
        validar_pesos(malos)
    with pytest.raises(ValueError):
        prevision(moneda(80, 80, "C", 50), pesos=malos)


def test_pesos_personalizados_se_aplican_y_un_peso_cero_quita_el_factor():
    p = prevision(moneda(90, 10, "E", 0), pesos={"Técnico corto plazo": 1.0, "Técnico largo plazo": 0.0})
    assert [f["nombre"] for f in p["factores"]] == ["Técnico corto plazo", "Semáforo de momento", "Flujo de órdenes"]
    assert p["score"] > 0 and abs(sum(f["aporte"] for f in p["factores"]) - p["score"]) < 1e-9


# ---------- equivalencia con la versión anterior cuando los datos están completos ----------
def test_resultados_identicos_a_la_version_anterior_con_todos_los_datos():
    a, b = completa(True), completa(False)
    assert a["score"] == pytest.approx(71.571429, abs=1e-6) and a["acuerdo"] == pytest.approx(1.0) and a["conf"] == "alta"
    assert b["score"] == pytest.approx(-68.171429, abs=1e-6) and b["dir"] == "bajista"
    assert a["objetivo"]["precio"] == 105 and a["invalidacion"]["precio"] == 93
    assert abs(sum(f["aporte"] for f in a["factores"]) - a["score"]) < 1e-9
    p = prevision(moneda(60, 40, "E", 10), pulso={"score": 10, "lectura": "Neutral", "n": 2})
    assert p["score"] == pytest.approx(4.107143, abs=1e-6) and p["acuerdo"] == pytest.approx(.475)


# ---------- NaN, infinitos y valores absurdos ----------
@pytest.mark.parametrize("campo", ["ts", "tl", "micro"])
@pytest.mark.parametrize("malo", [NAN, INF, -INF, "x", None, True])
def test_un_valor_no_finito_equivale_a_dato_ausente(campo, malo):
    o = moneda(80, 75, "C", 60)
    if campo == "micro":
        o["micro"]["score"] = malo
    else:
        o[campo] = malo
    esperado = moneda(80, 75, "C", 60)
    if campo == "micro":
        esperado["micro"]["score"] = None
    else:
        esperado[campo] = None
    a, b = prevision(o), prevision(esperado)
    assert a["score"] == pytest.approx(b["score"]) and a["faltan"] == b["faltan"] and not any(
        math.isnan(f["valor"]) for f in a["factores"])


def test_nan_ya_no_se_convierte_en_senal_alcista():
    # antes: _c(nan) = 1.0, así que un NaN en el técnico corto daba +1 (máximo alcista)
    sin_dato = prevision(moneda(None, 20, "V", -50))
    con_nan = prevision(moneda(NAN, 20, "V", -50))
    assert con_nan["score"] == pytest.approx(sin_dato["score"]) and con_nan["dir"] == "bajista"


@pytest.mark.parametrize("deriv", [{"ok": True, "funding": NAN, "largos_pct": 50}, {"ok": True, "funding": NAN, "largos_pct": NAN},
                                   {"ok": True}, {"ok": False, "funding": .001}, {}, None, "texto", 5])
def test_derivados_corruptos_o_ausentes(deriv):
    p = prevision(moneda(50, 50, "E", 0), deriv=deriv)
    assert p is not None
    if not (isinstance(deriv, dict) and deriv.get("ok") and deriv.get("largos_pct") == 50):
        assert "Derivados (contrario)" in p["faltan"]
    else:                                              # solo se pierde el funding: queda la parte de cuentas en largo
        assert "Derivados (contrario)" not in p["faltan"]


@pytest.mark.parametrize("pulso", [{"lectura": "x", "n": 1}, {"score": NAN}, {"score": None}, {"score": "a"}, {}, None, [], 3])
def test_noticias_incompletas_no_lanzan(pulso):
    p = prevision(moneda(50, 50, "E", 0), pulso=pulso)
    assert "Noticias 24 h" in p["faltan"]


@pytest.mark.parametrize("sent", [{"lectura": "x"}, {"valor": NAN}, {"valor": INF}, {}, None, 7])
def test_sentimiento_incompleto_no_lanza(sent):
    assert "Sentimiento (contrario)" in prevision(moneda(50, 50, "E", 0), sent=sent)["faltan"]


def test_semaforo_desconocido_o_malformado_se_ignora():
    for tm in ("X", None, 3, "", ["C"]):
        o = moneda(50, 50, "E", 0)
        o["tm"] = {"v": tm}
        assert "Semáforo de momento" in prevision(o)["faltan"]
    o = moneda(50, 50, "E", 0)
    o["tm"] = "C"
    assert "Semáforo de momento" in prevision(o)["faltan"]


def test_valores_extremos_se_acotan():
    p = prevision(moneda(1e9, -1e9, "C", 1e9))
    assert all(-1 <= f["valor"] <= 1 for f in p["factores"]) and -100 <= p["score"] <= 100
    p = prevision(moneda(50, 50, "E", 0), deriv={"ok": True, "funding": 1e6, "largos_pct": -1e6}, sent={"valor": 1e9, "lectura": "x"})
    assert all(-1 <= f["valor"] <= 1 for f in p["factores"])


@pytest.mark.parametrize("o", [None, {}, {"price": 0, "z": Z, "ts": 80}, {"price": NAN, "z": Z, "ts": 80}, {"price": -5, "z": Z, "ts": 80},
                               {"price": INF, "z": Z, "ts": 80}, {"price": 100, "z": {}, "ts": None}, {"price": 100, "z": Z},
                               {"price": 100, "z": None, "ts": 80}, "x", 3])
def test_entrada_invalida_devuelve_none(o):
    assert prevision(o) is None


def test_zonas_corruptas_se_ignoran_sin_romper_el_resultado():
    o = moneda(80, 80, "C", 60)
    o["z"] = {"sup": [{"lo": NAN, "hi": 97, "mid": 96}, {"lo": 90}], "res": [{"lo": 104, "hi": 106, "mid": 105}, "x"]}
    p = prevision(o)
    assert p["dir"] == "alcista" and p["objetivo"]["precio"] == 105 and p["invalidacion"] is None
    o["z"] = {"sup": None, "res": None}
    assert prevision(o)["objetivo"] is None
    o["z"] = {}
    assert prevision(o)["objetivo"] is None


def test_la_salida_siempre_es_json_estricto():
    for o in (moneda(NAN, INF, "C", NAN), moneda(80, 75, "C", 60)):
        p = prevision(o, deriv={"ok": True, "funding": NAN, "largos_pct": 70}, pulso={"score": INF, "n": 1}, sent={"valor": NAN})
        json.dumps(p, allow_nan=False)                 # JavaScript no entiende NaN: no puede colarse nunca


# ---------- cobertura ----------
def test_cobertura_completa_y_parcial():
    p = completa()
    assert p["cobertura"] == pytest.approx(1.0) and p["n_factores"] == 7 and p["n_total"] == 7 and p["faltan"] == []
    q = prevision(moneda(80, 75, "C", 60), pulso={"score": 35, "lectura": "Positivo", "n": 4})
    assert q["n_factores"] == 5 and q["cobertura"] == pytest.approx(.80) and q["faltan"] == ["Derivados (contrario)", "Sentimiento (contrario)"]


def test_dos_factores_ya_no_parecen_tan_fiables_como_siete():
    solo2 = prevision({"price": 100, "z": Z, "ts": 95, "tl": 95, "d1": {"atr": 2}})
    todos = prevision(moneda(95, 95, "C", 100), deriv={"ok": True, "funding": -.0004, "largos_pct": 30},
                      pulso={"score": 70, "lectura": "Positivo", "n": 5}, sent={"valor": 5, "lectura": "Miedo extremo"})
    assert solo2["cobertura"] == pytest.approx(.44) and solo2["cobertura"] < UMBRAL_COBERTURA_BAJA
    assert solo2["conf"] == "baja" and todos["conf"] == "alta"          # antes las dos salían «alta»
    assert solo2["score"] > 90 and todos["score"] > 90                   # la intensidad no cambia: solo la fiabilidad aparente


def test_cobertura_intermedia_limita_alta_a_media():
    # técnico corto+largo (44 %) + semáforo (10 %) + flujo (10 %) = 64 %: entre 50 % y 75 %
    p = prevision(moneda(95, 95, "C", 100))
    assert .5 <= p["cobertura"] < .75 and p["conf"] == "media"


def test_sin_dato_no_es_lo_mismo_que_neutral():
    neutral = prevision(moneda(50, 50, "E", 0), pulso={"score": 0, "lectura": "Neutral", "n": 5, "n_lectura": 5})
    ausente = prevision(moneda(50, 50, "E", 0), pulso={"score": 0, "lectura": "Neutral", "n": 5, "n_lectura": 0})
    assert "Noticias 24 h" not in neutral["faltan"] and "Noticias 24 h" in ausente["faltan"]
    assert neutral["cobertura"] > ausente["cobertura"]
    # un 0 de «noticias neutrales» pesa en el promedio; la falta de noticias no
    base = moneda(90, 90, "C", 80)
    con_neutral = prevision(base, pulso={"score": 0, "lectura": "Neutral", "n": 5, "n_lectura": 5})
    sin_noticias = prevision(base, pulso={"score": 0, "lectura": "Neutral", "n": 5, "n_lectura": 0})
    assert con_neutral["score"] < sin_noticias["score"]


def test_pulso_antiguo_sin_n_lectura_cuenta_como_antes():
    assert "Noticias 24 h" not in prevision(moneda(50, 50, "E", 0), pulso={"score": -50, "lectura": "Negativo", "n": 5})["faltan"]


# ---------- intensidad, acuerdo y probabilidad ----------
def test_intensidad_acuerdo_y_probabilidad_son_conceptos_distintos():
    p = completa()
    assert p["intensidad"] == pytest.approx(abs(p["score"])) and 0 <= p["acuerdo"] <= 1
    assert p["prob_calibrada"] is None and p["calibrada"] is False and "probabilidad" in p["nota"]
    assert p["intensidad"] != pytest.approx(p["acuerdo"] * 100)           # 71,6 de intensidad no es un 71,6 % de probabilidad
    assert not any(k in p for k in ("prob", "probabilidad"))


def test_acuerdo_bajo_con_factores_enfrentados():
    p = prevision(moneda(95, 5, "E", 0), deriv=DERIV_BAJISTA, pulso={"score": 60, "lectura": "Positivo", "n": 4, "n_lectura": 4})
    assert p["acuerdo"] < .6 and p["conf"] == "baja"


@pytest.mark.parametrize("regimen,ts,tl,tm,mi,dir_", [("alcista", 85, 80, "C", 70, "alcista"), ("bajista", 15, 20, "V", -70, "bajista"),
                                                         ("lateral", 50, 50, "E", 0, "lateral"), ("giro", 20, 80, "E", 0, "lateral")])
def test_regimenes(regimen, ts, tl, tm, mi, dir_):
    p = prevision(moneda(ts, tl, tm, mi))
    assert p["dir"] == dir_ and p["conf"] in ("alta", "media", "baja")


def test_factores_publicos_mantienen_la_estructura_que_lee_la_interfaz():
    f = factores(moneda(80, 75, "C", 60), DERIV_ALCISTA, {"score": 35, "lectura": "Positivo", "n": 4}, {"valor": 25, "lectura": "Miedo"})
    assert len(f) == 7 and all({"nombre", "valor", "peso", "dato"} <= set(x) for x in f)
    p = completa()
    for k in ("score", "dir", "conf", "acuerdo", "objetivo", "invalidacion", "factores", "a_favor", "en_contra", "horizonte"):
        assert k in p
    assert all("aporte" in x for x in p["factores"])
