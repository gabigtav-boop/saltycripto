/* SaltyCripto · lectura spot en el navegador (bloque A de la versión de estabilidad).
   UNA sola definición de «activo apto» para portada, tabla, ficha y avisos:
     apto = hay decisión del motor, la cotización tiene como mucho 120 s, no viene del futuro, la decisión no ha caducado
            y la acción no es ND (el motor ya exige cobertura de factores ≥ 0,50 y previsión recalculada).
   La caducidad se vuelve a evaluar aquí con el reloj del navegador, aunque dejen de llegar mensajes del motor.
   Sin dependencias: se prueba en Node (tests/estabilidad.mjs). */
(function (raiz) {
  "use strict";
  const FRESCO_MS = 120000;

  /** ¿La cotización con hora pt sirve ahora? Una marca futura no vale; 120 s exactos sí; 121 s ya no. */
  function precioVigente(pt, ahora) {
    return typeof pt === "number" && pt > 0 && pt <= ahora && ahora - pt <= FRESCO_MS;
  }

  /** Lectura vigente de un activo. Nunca devuelve una acción C/E/V con un dato vencido. */
  function lectura(o, ahora) {
    const d = o && o.decision;
    const pt = o && o.precio_t;
    const base = { zona: null, precio_t: pt || null, revisar: null, anterior: null, apto: false };
    if (!o) return Object.assign(base, { accion: "ND", estado: "sin_dato", motivo: "Sin datos de este activo",
      revisar: "Se evaluará cuando el motor cargue este activo." });
    if (typeof pt === "number" && pt > ahora)
      return Object.assign(base, { accion: "ND", estado: "futura", motivo: "Hora de la cotización posterior a la actual: no es válida",
        revisar: "Se evaluará con una cotización de hora válida.", anterior: d && d.accion !== "ND" ? resumenPrevia(d) : (d && d.anterior) || null });
    if (!d) return Object.assign(base, { accion: "ND", estado: "sin_dato", motivo: "Decisión todavía no calculada",
      revisar: "Se evaluará cuando llegue el primer cálculo del motor." });
    const vence = typeof d.caduca === "number" ? d.caduca : (pt || 0) + FRESCO_MS;
    if (d.accion !== "ND" && (!precioVigente(pt, ahora) || ahora > vence))
      // el motor la calculó con un dato que ya venció: se conserva como lectura anterior, no como vigente
      return Object.assign(base, { accion: "ND", estado: "caducada", motivo: "Cotización pendiente de renovar",
        revisar: "Se evaluará al recibir una cotización de menos de 120 s.", anterior: resumenPrevia(d) });
    if (d.accion === "ND")
      return Object.assign({}, base, d, { estado: "sin_dato", apto: false, anterior: d.anterior || null,
        revisar: d.revisar || "Se evaluará al renovar los datos." });
    return Object.assign({}, base, d, { estado: "vigente", apto: true, anterior: null });
  }

  function resumenPrevia(d) {
    return { accion: d.accion, motivo: d.motivo, precio_t: d.precio_t || null, zona: d.zona || null };
  }

  /** Texto de la acción según la situación del usuario: una salida presupone que posee el activo. */
  function texto(accion, conPosicion) {
    if (accion === "C") return conPosicion ? "Revisar ampliación" : "Valorar entrada";
    if (accion === "V") return conPosicion ? "Revisar salida" : "Evitar entrada";
    if (accion === "E") return conPosicion ? "Mantener en observación" : "Esperar";
    return "Pendiente de datos";      // ND: no se ha podido evaluar (distinto de una espera evaluada)
  }

  const lista = L => L.length > 1 ? L.slice(0, -1).join(", ") + " y " + L[L.length - 1] : (L[0] || "");

  /** Resumen agregado para la portada. Solo cuenta los activos cripto del catálogo (cr !== false); el resto (PAXG)
      se describe aparte. «Ninguna entrada favorable» se refiere SOLO a los activos evaluados. */
  function resumen(simbolos, DATA, COINS, conPos, ahora, etiqueta) {
    const et = etiqueta || (s => s);
    const cr = simbolos.filter(s => COINS[s] && COINS[s].cr !== false);
    const nc = simbolos.filter(s => COINS[s] && COINS[s].cr === false);
    const L = {};
    for (const s of simbolos) L[s] = lectura(DATA[s], ahora);
    const aptos = cr.filter(s => L[s].apto), faltan = cr.filter(s => !L[s].apto);
    const g = { entrada: [], ampliar: [], salida: [], evitar: [], espera: [] };
    for (const s of aptos) {
      const a = L[s].accion, p = !!conPos(s);
      if (a === "C") g[p ? "ampliar" : "entrada"].push(s);
      else if (a === "V") g[p ? "salida" : "evitar"].push(s);
      else g.espera.push(s);
    }
    let titular, tono;
    if (!aptos.length) { titular = "Decisión pendiente de datos"; tono = "warn"; }
    else if (g.entrada.length) { titular = "Valorar entrada en " + lista(g.entrada.map(et)); tono = "pos"; }
    else if (g.ampliar.length) { titular = "Revisar ampliación en " + lista(g.ampliar.map(et)); tono = "pos"; }
    else {
      titular = faltan.length ? `Ninguna entrada favorable entre los ${aptos.length} evaluados` : "Ninguna entrada favorable ahora";
      tono = "";
    }
    const lineas = [];
    if (g.salida.length) lineas.push("Revisar salida en " + lista(g.salida.map(et)) + ".");
    if (g.evitar.length) lineas.push("Evitar entrada en " + lista(g.evitar.map(et)) + ".");
    if (aptos.length && !g.salida.length && !g.evitar.length) lineas.push("Sin salidas indicadas entre los evaluados.");
    if (faltan.length && aptos.length) lineas.push(`Evaluados ${aptos.length} de ${cr.length}; sin datos suficientes: ${lista(faltan.map(et))}.`);
    if (!aptos.length && cr.length) lineas.push(`Ninguno de los ${cr.length} activos cripto tiene datos vigentes para evaluar.`);
    const aparte = nc.map(s => ({ s, apto: L[s].apto, texto: L[s].apto ? texto(L[s].accion, !!conPos(s)) : "Pendiente de datos" }));
    return { titular, tono, lineas, aptos, faltan, grupos: g, cobertura: [aptos.length, cr.length], aparte, lecturas: L };
  }

  const API = { FRESCO_MS, precioVigente, lectura, texto, resumen, lista };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  raiz.SPOT = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
