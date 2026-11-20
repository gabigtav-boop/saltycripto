"""Versión inequívoca de esta copia de SaltyCripto (archivo VERSION en la raíz). El lanzador (abrir.sh) la compara con lo
que responde /api/health antes de abrir el navegador: así no se abre una copia distinta ni una página de error."""
import os
import pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[1]
APP = "SaltyCripto"
try:
    VERSION = (RAIZ / "VERSION").read_text().strip()
except OSError:
    VERSION = "desconocida"


def identidad():
    return {"app": APP, "version": VERSION, "raiz": str(RAIZ), "pid": os.getpid()}
