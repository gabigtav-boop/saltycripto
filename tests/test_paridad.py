"""Paridad total con la v21: el motor Python debe dar exactamente los mismos resultados que el JavaScript
original sobre la misma instantánea real del mercado (lecturas, puntuaciones, zonas, semáforo, fiabilidad,
histórico y validación). Requiere Node.js."""
import json
import math
import pathlib
import shutil
import subprocess

import pytest

from cripto import fuentes
from cripto.motor import analyze, bias, fin, validate, zones
from cripto.servicio import CGID, COINS, Servicio, clean

HERE = pathlib.Path(__file__).parent
FIX = HERE / "fixtures" / "mercado.json"


class FakeKraken:
    def __init__(self, fx):
        self.fx = fx

    def get(self, p):
        if p.startswith("Depth?pair="):
            return self.fx["depth"][p[11:].split("&")[0]]
        if p.startswith("Ticker?pair="):
            return self.fx["ticker"]
        if p.startswith("OHLC?pair="):
            key, mins = p[10:].split("&interval=")
            return self.fx["ohlc"][f"{key}|{mins}"]
        raise KeyError(p)


def proj(data):
    out = {}
    for s, o in data.items():
        d = {k: v for k, v in o.items() if k not in ("d1", "raw1d") and not k.startswith("_")}
        if "res" in d:
            d["res"] = {k: {kk: vv for kk, vv in x.items() if kk not in ("rows", "E20", "E50")} for k, x in d["res"].items()}
        out[s] = d
    return json.loads(json.dumps(clean(out)))


def python_side(fx):
    sv = Servicio(kraken=FakeKraken(fx))
    sv.pairs = fx["pairs"]
    sv.tk = fx["ticker"]
    sv.cbp = {s: float(v["data"]["amount"]) for s, v in fx["cbp"].items()}
    sv.mk = {}
    for s, cid in CGID.items():
        x = next((y for y in fx["mk"] if y["id"] == cid), None)
        if x:
            sv.mk[s] = {"p": x["current_price"], "mc": x["market_cap"], "rk": x["market_cap_rank"], "ch": x["price_change_percentage_24h"]}
    sv.order = sorted(COINS, key=lambda s: -((sv.mk.get(s) or {}).get("mc") or 0))
    sv.cbc = {}
    for s, d in fx["cbc"].items():
        a = analyze(fuentes.parse_coinbase(d))
        if a and a["score"] is not None:
            sv.cbc[s] = {"b": bias(a["score"])}
    sv.ohlc = lambda key, mins: fuentes.parse_ohlc(fx["ohlc"][f"{key}|{mins}"])
    keys = ["BTC"] + [k for k in sv.order if k != "BTC"]
    for s in keys:
        o = sv.run(s, now=fx["now"])
        sv.data[s] = o
        if s == "BTC" and o.get("bL"):
            sv.btcl = o["bL"]
    sv.refresh_src()
    first = proj(sv.data)
    for s in keys:
        sv.add_bt(s)
    val = validate(sv.data)
    with_bt = proj(sv.data)
    for s, o in sv.data.items():
        if not o.get("d1"):
            continue
        p = o["price"] * 1.007
        o["price"] = p
        if o.get("ref24"):
            o["chg"] = (p / o["ref24"] - 1) * 100
        o["z"] = zones(o["d1"], p, o.get("book"), o["res"].get("1w"))
        fin(o)
    return {"order": sv.order, "cbc": sv.cbc, "first": first, "withBt": with_bt, "live": proj(sv.data),
            "val": json.loads(json.dumps(clean(val)))}


def diff(a, b, path="", out=None, tol=1e-9):
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            if a.get(k) is None and b.get(k) is None:
                continue
            diff(a.get(k), b.get(k), f"{path}.{k}", out, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: longitud {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diff(x, y, f"{path}[{i}]", out, tol)
    elif isinstance(a, bool) or isinstance(b, bool):
        if a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not math.isclose(a, b, rel_tol=tol, abs_tol=1e-12):
            out.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
    return out


@pytest.fixture(scope="module")
def ambos():
    if not shutil.which("node"):
        pytest.skip("Node.js no está instalado")
    fx = json.loads(FIX.read_text())
    js = json.loads(subprocess.run(["node", str(HERE / "paridad_v21.mjs"), str(FIX)], capture_output=True, check=True, text=True).stdout)
    return js, python_side(fx)


@pytest.mark.parametrize("fase", ["order", "cbc", "first", "withBt", "live", "val"])
def test_paridad(ambos, fase):
    js, py = ambos
    d = diff(js[fase], py[fase], fase)
    assert not d, f"{len(d)} diferencias:\n" + "\n".join(sorted(d)[:40])


def test_cobertura(ambos):
    """La instantánea cubre los casos importantes, para que la paridad no sea trivial."""
    js, _ = ambos
    live = js["live"]
    assert len(live) == 8
    assert any(o.get("errs") for o in live.values()), "algún gráfico sin datos"
    assert any(o.get("bt") is None for o in live.values()), "alguna moneda sin histórico suficiente"
    assert sum(1 for o in live.values() if o.get("bt")) >= 5
    assert js["val"]["abl"], "aporte de indicadores calculado"
