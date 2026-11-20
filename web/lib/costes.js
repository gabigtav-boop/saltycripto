/* SaltyCripto · coste de comprar o vender 25, 50 o 100 € en el navegador (solo la web publicada).
   Traducción directa de cripto/costes.py (en el Mac lo calcula el servidor en /api/coste): mismas reglas, mismos estados y
   mismos textos. tests/test_estabilidad.py compara las dos versiones caso a caso. Sin dependencias: se prueba en Node.
   Convención: el importe es el NOMINAL (sin comisión); la comisión se paga aparte en una compra y se descuenta en una venta;
   el desplazamiento se mide desde el punto medio del libro (ya incluye la media horquilla). */
(function (raiz) {
  "use strict";
  const EDAD_MAX_MS = 120000;
  const IMPORTES = [25, 50, 100];
  const REFERENCIAS = {
    fecha: "2026-11-20",
    aviso: "Tu comisión real depende del par, del producto (Kraken Pro u otro) y de tu nivel de volumen: compruébala en tu cuenta.",
  };

  const equilibrio = (fc, fv) => (1 + fc) / (1 - fv) - 1;
  const floorDec = (x, dec) => { const k = 10 ** Math.trunc(dec); return Math.floor(x * k + 1e-9) / k; };

  function recorrer(niveles, cantidad) {
    let falta = cantidad, eur = 0, usados = 0;
    for (const [p, q] of niveles) {
      if (falta <= 1e-15) break;
      const x = Math.min(q, falta);
      eur += x * p; falta -= x; usados += 1;
    }
    return [cantidad - Math.max(falta, 0), eur, usados];
  }

  function cantidadPara(niveles, nominal) {
    let falta = nominal, q = 0;
    for (const [p, qq] of niveles) {
      if (falta <= 1e-12) break;
      const x = Math.min(qq, falta / p);
      q += x; falta -= x * p;
    }
    return [q, falta];
  }

  function estimar(importe, lado, libro, ahora, reglas, maker = .004, taker = .008, tipo = "mercado", limite = null) {
    reglas = reglas || {};
    const base = { importe, lado, tipo, maker, taker, equilibrio_taker: equilibrio(taker, taker), equilibrio_maker_taker: equilibrio(maker, taker) };
    if (!(typeof importe === "number" && isFinite(importe) && importe > 0))
      return Object.assign(base, { estado: "importe_invalido", motivo: "El importe debe ser un número positivo." });
    if (!libro || !libro.asks || !libro.asks.length || !libro.bids || !libro.bids.length)
      return Object.assign(base, { estado: "sin_libro", motivo: "Sin libro ni mejor bid/ask vigente: no se puede estimar el coste." });
    const asks = libro.asks.slice().sort((a, b) => a[0] - b[0]), bids = libro.bids.slice().sort((a, b) => b[0] - a[0]);
    const ba = asks[0][0], bb = bids[0][0], medio = (ba + bb) / 2, edad = ahora - (libro.t || 0);
    Object.assign(base, { captura_t: libro.t, edad_s: Math.round(edad / 100) / 10, medio, mejor_ask: ba, mejor_bid: bb,
      horquilla_pct: (ba - bb) / medio, fuente: libro.fuente });
    const ladoNiv = lado === "compra" ? asks : bids;
    let cruza = null;
    if (tipo === "limite") cruza = limite != null && ((lado === "compra" && limite >= ba) || (lado === "venta" && limite <= bb));
    const tasa = tipo === "mercado" || cruza ? taker : maker;
    Object.assign(base, { tipo_comision: tasa === taker && (tipo === "mercado" || cruza) ? "taker" : "maker", cruza, tasa });
    if (edad < 0 || edad > EDAD_MAX_MS)
      return Object.assign(base, { estado: "caducado", comision: importe * tasa,
        motivo: `Libro de hace ${Math.round(edad / 1000)} s (máximo ${EDAD_MAX_MS / 1000} s): solo se muestra la comisión.` });
    const lot = reglas.lot_decimals != null ? reglas.lot_decimals : 8;
    let out;
    if (tipo === "limite" && !cruza) {
      const p = limite || (lado === "compra" ? bb : ba), q = floorDec(importe / p, lot), nominal = q * p;
      out = Object.assign(base, { estado: "pasiva", cantidad: q, precio_medio: p, nominal, comision: nominal * tasa, desplazamiento: 0,
        motivo: "Orden limitada que no cruza el libro: paga maker si se ejecuta; la ejecución no está garantizada." });
    } else {
      let q, sobra;
      if (lado === "compra") [q, sobra] = cantidadPara(ladoNiv, importe);
      else { q = importe / medio; sobra = 0; }
      q = floorDec(q, lot);
      const [cubierta, eur, usados] = recorrer(ladoNiv, q);
      if (libro.completo === false) {
        const pm = ladoNiv[0][0], nominal = q * pm;
        return Object.assign(base, { estado: "parcial", cantidad: q, precio_medio: pm, nominal, comision: nominal * tasa,
          desplazamiento: Math.abs(pm - medio) * q, niveles_usados: 1,
          motivo: "Solo se conoce el mejor bid/ask: comisión y media horquilla; falta la profundidad del libro para el resto." });
      }
      if (cubierta + 1e-12 < q || sobra > 1e-9)
        return Object.assign(base, { estado: "insuficiente", cantidad: cubierta, nominal: eur, niveles_usados: usados,
          motivo: `El libro recibido (${ladoNiv.length} niveles) no alcanza para todo el importe.` });
      const pm = q ? eur / q : null;
      out = Object.assign(base, { estado: "ejecutable", cantidad: q, precio_medio: pm, nominal: eur, comision: eur * tasa,
        desplazamiento: lado === "compra" ? (pm - medio) * q : (medio - pm) * q, niveles_usados: usados,
        motivo: "Estimación con el libro recibido: no es una ejecución." });
    }
    if (out.cantidad <= 0 || (reglas.ordermin && out.cantidad < reglas.ordermin) || (reglas.costmin && out.nominal < reglas.costmin))
      Object.assign(out, { estado: "bajo_minimo",
        motivo: `Por debajo del mínimo del par (cantidad mínima ${reglas.ordermin}, nominal mínimo ${reglas.costmin} €).` });
    out.coste_total = out.comision + (out.desplazamiento || 0);
    out.coste_pct = out.nominal ? out.coste_total / out.nominal : null;
    out.total = lado === "compra" ? out.nominal + out.comision : out.nominal - out.comision;
    return out;
  }

  function tabla(lado, libro, ahora, reglas, maker, taker, tipo, limite, importes) {
    return { lado, filas: (importes || IMPORTES).map(i => estimar(i, lado, libro, ahora, reglas, maker, taker, tipo, limite)),
      referencias: REFERENCIAS };
  }

  const API = { EDAD_MAX_MS, IMPORTES, REFERENCIAS, equilibrio, estimar, tabla };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  raiz.COSTES = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
