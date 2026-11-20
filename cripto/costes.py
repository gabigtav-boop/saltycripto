"""Coste de comprar o vender 25, 50 o 100 € en el par Kraken/EUR del catálogo (bloque D de la versión de estabilidad).

Convención (la misma en toda la app):
  · el importe introducido es el NOMINAL de la orden (valor de las monedas, sin comisión);
  · la comisión se calcula sobre el nominal ejecutado: en una compra se paga aparte (25 € al 0,80 % → 0,20 €; total 25,20 €)
    y en una venta se descuenta de lo cobrado;
  · el desplazamiento se mide desde el punto medio del libro hasta el precio medio ponderado de la ejecución: ya incluye la
    media horquilla, así que la horquilla NO se suma otra vez;
  · una orden limitada que no cruza el libro es pasiva (maker) y su ejecución no está garantizada; si cruza, paga taker;
  · el evaluador spot (estudios/evaluar_spot.py) compara alternativas con el mismo presupuesto con la comisión incluida, que
    es la misma aritmética: comprar y vender sin moverse el precio exige (1+f_compra)/(1−f_venta) − 1.
Las tarifas son escenarios configurables: dependen del par y del nivel de la cuenta. Los valores de referencia están fechados
y etiquetados; no son la tarifa del usuario."""
import math

EDAD_MAX_MS = 120_000
IMPORTES = (25, 50, 100)
REFERENCIAS = {
    "fecha": "2026-11-20",
    "escenarios": {
        "estudio": {"maker": .004, "taker": .008, "etiqueta": "Escenario del estudio del 20 nov 2026: 0,40 % maker / 0,80 % taker"},
        "hist": {"maker": .001, "taker": .001, "etiqueta": "Comparador histórico del estudio: 0,10 % por lado"},
    },
    "aviso": "Tu comisión real depende del par, del producto (Kraken Pro u otro) y de tu nivel de volumen: compruébala en tu cuenta.",
}


def equilibrio(f_compra, f_venta):
    """Subida necesaria para recuperar el nominal tras comprar y vender (sin otras fricciones)."""
    return (1 + f_compra) / (1 - f_venta) - 1


def _floor(x, dec):
    k = 10 ** int(dec)
    return math.floor(x * k + 1e-9) / k


def _recorrer(niveles, cantidad):
    """Ejecuta `cantidad` contra los niveles [(precio, cantidad)] en orden. Devuelve (cantidad cubierta, euros, niveles usados)."""
    falta, eur, usados = cantidad, 0.0, 0
    for p, q in niveles:
        if falta <= 1e-15:
            break
        x = min(q, falta)
        eur += x * p
        falta -= x
        usados += 1
    return cantidad - max(falta, 0), eur, usados


def _cantidad_para(niveles, nominal):
    """Cantidad que se obtiene gastando `nominal` euros contra los niveles (compra)."""
    falta, q_tot = nominal, 0.0
    for p, q in niveles:
        if falta <= 1e-12:
            break
        x = min(q, falta / p)
        q_tot += x
        falta -= x * p
    return q_tot, falta


def estimar(importe, lado, libro, ahora_ms, reglas=None, maker=.004, taker=.008, tipo="mercado", limite=None):
    """Estimación para un nominal. `libro` = {"asks": [(p, q)…], "bids": [(p, q)…], "t": ms de captura, "completo": bool}
    (completo=False si solo se conoce el mejor bid/ask, p. ej. por el ticker). `reglas` = {"ordermin", "costmin",
    "lot_decimals", "pair_decimals"} del par en Kraken. Devuelve un diccionario con `estado`:
    ejecutable · parcial (sin profundidad) · insuficiente (el libro no alcanza) · bajo_minimo · caducado · sin_libro."""
    reglas = reglas or {}
    base = {"importe": importe, "lado": lado, "tipo": tipo, "maker": maker, "taker": taker,
            "equilibrio_taker": equilibrio(taker, taker), "equilibrio_maker_taker": equilibrio(maker, taker)}
    if not (isinstance(importe, (int, float)) and math.isfinite(importe) and importe > 0):
        return dict(base, estado="importe_invalido", motivo="El importe debe ser un número positivo.")
    if not libro or not libro.get("asks") or not libro.get("bids"):
        return dict(base, estado="sin_libro", motivo="Sin libro ni mejor bid/ask vigente: no se puede estimar el coste.")
    asks, bids = sorted(libro["asks"]), sorted(libro["bids"], reverse=True)
    ba, bb = asks[0][0], bids[0][0]
    medio = (ba + bb) / 2
    edad = ahora_ms - (libro.get("t") or 0)
    base.update(captura_t=libro.get("t"), edad_s=round(edad / 1000, 1), medio=medio, mejor_ask=ba, mejor_bid=bb,
                horquilla_pct=(ba - bb) / medio, fuente=libro.get("fuente"))
    lado_niv = asks if lado == "compra" else bids
    cruza = None
    if tipo == "limite":
        cruza = limite is not None and ((lado == "compra" and limite >= ba) or (lado == "venta" and limite <= bb))
    tasa = taker if tipo == "mercado" or cruza else maker
    base.update(tipo_comision="taker" if tasa == taker and (tipo == "mercado" or cruza) else "maker", cruza=cruza, tasa=tasa)
    if edad < 0 or edad > EDAD_MAX_MS:
        return dict(base, estado="caducado", comision=importe * tasa,
                    motivo=f"Libro de hace {round(edad / 1000)} s (máximo {EDAD_MAX_MS // 1000} s): solo se muestra la comisión.")
    lot = reglas.get("lot_decimals", 8)
    if tipo == "limite" and not cruza:
        # orden pasiva: sin desplazamiento, pero sin garantía de ejecución
        p = limite or (bb if lado == "compra" else ba)
        q = _floor(importe / p, lot)
        nominal = q * p
        out = dict(base, estado="pasiva", cantidad=q, precio_medio=p, nominal=nominal, comision=nominal * tasa,
                   desplazamiento=0.0, motivo="Orden limitada que no cruza el libro: paga maker si se ejecuta; la ejecución no está garantizada.")
    else:
        if lado == "compra":
            q, sobra = _cantidad_para(lado_niv, importe)
        else:
            q, sobra = importe / medio, 0.0
        q = _floor(q, lot)
        cubierta, eur, usados = _recorrer(lado_niv, q)
        if not libro.get("completo", True):
            pm = lado_niv[0][0]
            nominal = q * pm
            return dict(base, estado="parcial", cantidad=q, precio_medio=pm, nominal=nominal, comision=nominal * tasa,
                        desplazamiento=abs(pm - medio) * q, niveles_usados=1,
                        motivo="Solo se conoce el mejor bid/ask: comisión y media horquilla; falta la profundidad del libro para el resto.")
        if cubierta + 1e-12 < q or sobra > 1e-9:
            return dict(base, estado="insuficiente", cantidad=cubierta, nominal=eur, niveles_usados=usados,
                        motivo=f"El libro recibido ({len(lado_niv)} niveles) no alcanza para todo el importe.")
        pm = eur / q if q else None
        out = dict(base, estado="ejecutable", cantidad=q, precio_medio=pm, nominal=eur, comision=eur * tasa,
                   desplazamiento=(pm - medio) * q if lado == "compra" else (medio - pm) * q, niveles_usados=usados,
                   motivo="Estimación con el libro verificado: no es una ejecución.")
    if out["cantidad"] <= 0 or (reglas.get("ordermin") and out["cantidad"] < reglas["ordermin"]) or \
            (reglas.get("costmin") and out["nominal"] < reglas["costmin"]):
        out.update(estado="bajo_minimo", motivo=f"Por debajo del mínimo del par (cantidad mínima {reglas.get('ordermin')}, "
                                               f"nominal mínimo {reglas.get('costmin')} €).")
    out["coste_total"] = out["comision"] + out.get("desplazamiento", 0.0)
    out["coste_pct"] = out["coste_total"] / out["nominal"] if out.get("nominal") else None
    out["total"] = out["nominal"] + out["comision"] if lado == "compra" else out["nominal"] - out["comision"]
    return out


def tabla(lado, libro, ahora_ms, reglas=None, maker=.004, taker=.008, tipo="mercado", limite=None, importes=IMPORTES):
    return {"lado": lado, "filas": [estimar(i, lado, libro, ahora_ms, reglas, maker, taker, tipo, limite) for i in importes],
            "referencias": REFERENCIAS, "convencion": __doc__.split("Convención (la misma en toda la app):")[1].split("Las tarifas")[0].strip()}
