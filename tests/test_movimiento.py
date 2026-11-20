"""Movimiento de la esfera de la portada (ANALISIS_ANIMACION.md): muelles exactos, curvas y cadencia."""
import pathlib
import re
import subprocess

RAIZ = pathlib.Path(__file__).resolve().parents[1]
HTML = (RAIZ / "web" / "index.html").read_text()


def test_pruebas_de_movimiento_en_node():
    r = subprocess.run(["node", str(RAIZ / "tests" / "movimiento.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "comprobaciones correctas" in r.stdout


def test_la_pagina_usa_la_libreria_de_movimiento():
    assert '<script src="lib/movimiento.js"></script>' in HTML
    assert HTML.index("lib/movimiento.js") < HTML.index("function crearNucleo(")
    i = HTML.index("function crearNucleo(")
    n = HTML[i:HTML.index("/* ---------- portada de Mercado", i)]
    assert len(n) > 5000
    # sin interpolaciones por fotograma (dependen de los fps) ni temporizador aparte para la onda del paquete
    assert not re.search(r"par\.x\+=\(par\.tx-par\.x\)\*", n)
    assert "setTimeout(" not in n
    assert "MOV.muelle(" in n and "arco(" in n and "eio(" in n
    # la cadencia sigue siendo un múltiplo del refresco y se limita el paso tras una pausa
    assert "1000/60:1000/30" in n and "Math.min(.064" in n


def test_se_sirve_la_libreria():
    assert (RAIZ / "web" / "lib" / "movimiento.js").exists()
