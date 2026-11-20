/* SaltyCripto · contrato de cantidades de la cartera (bloque B de la versión de estabilidad).
   Tres tipos de dato:  qty   cantidad de cripto (unidades del activo)
                        eur   importe en euros (invertido, objetivo, pérdida máxima)
                        price precio unitario en euros
   Reglas de lectura (formato español primero):
     · con coma y punto: el que aparece el último es el decimal; el otro agrupa miles en bloques de 3 («1.500,25», «1,500.25»);
     · solo comas: una coma es decimal («0,0031»); varias, miles bien agrupados;
     · solo puntos: varios = miles bien agrupados («1.234.567»); uno seguido de exactamente 3 cifras con parte entera
       1-999 («1.234», «12.500») es AMBIGUO: se ofrecen las dos lecturas y no se aplica nada hasta elegir; en el resto el
       punto es decimal («0.0031», «1.5», «1500.25»);
     · cualquier otro carácter (letras, unidades, signos) es un error: no se aceptan prefijos parciales («1.25BTC»).
   El valor confirmado se guarda como número y texto en `ci_pos`; nunca se recalcula a partir del texto formateado en
   pantalla. Un texto antiguo (formato anterior: cadena) se lee con estas reglas sin reescribirse; si es ambiguo o no se
   puede leer, queda señalado para revisión. */
(function (raiz) {
  "use strict";
  const NOMBRE = { qty: "cantidad", eur: "importe", price: "precio" };
  const MAX_DIG = 15;   // por encima se pierde precisión en coma flotante

  const fmt = (v, kind) => new Intl.NumberFormat("es-ES", {
    minimumFractionDigits: kind === "qty" ? 0 : 2,
    maximumFractionDigits: kind === "qty" ? 10 : (v < 1 ? 8 : v < 10 ? 6 : 2), useGrouping: true }).format(v);

  const unidad = (kind, sym) => kind === "qty" ? " " + (sym || "unidades") : " €";

  function gruposOk(entera, sep) {
    const g = entera.split(sep);
    return /^\d{1,3}$/.test(g[0]) && g.slice(1).every(x => /^\d{3}$/.test(x));
  }

  function aNumero(enteraSinSep, decimales) {
    const t = enteraSinSep.replace(/^0+(?=\d)/, "") + (decimales ? "." + decimales : "");
    if (t.replace(".", "").replace(/^0+/, "").length > MAX_DIG) return null;
    const v = Number(t);
    return Number.isFinite(v) ? v : null;
  }

  /** Lee un texto. Devuelve uno de:
      {estado:'vacio'} · {estado:'ok', v} · {estado:'ambiguo', opciones:[{v, etiqueta}, …]} · {estado:'error', error} */
  function leer(texto, kind, sym) {
    const k = kind || "qty";
    let t = String(texto == null ? "" : texto).replace(/[\s  ]/g, "");
    if (k !== "qty") t = t.replace(/€$/, "").replace(/^€/, "");
    if (!t) return { estado: "vacio" };
    const campo = NOMBRE[k] || "valor";
    if (/^[-−]/.test(t)) return { estado: "error", error: `El ${campo} debe ser positivo.` };
    if (!/^\d[\d.,]*$/.test(t) || /[.,]$/.test(t))
      return { estado: "error", error: `Escribe solo el número del ${campo}${k === "qty" ? " (sin la unidad)" : ""}, p. ej. ${k === "qty" ? "0,0031" : "1.500,25"}.` };
    const nc = (t.match(/,/g) || []).length, np = (t.match(/\./g) || []).length;
    let v = null, ambiguo = null;
    if (nc && np) {
      const dec = t.lastIndexOf(",") > t.lastIndexOf(".") ? "," : ".", mil = dec === "," ? "." : ",";
      const [ent, decs, resto] = t.split(dec);
      if (resto !== undefined || !gruposOk(ent, mil)) return { estado: "error", error: `Separadores mal colocados en el ${campo}.` };
      v = aNumero(ent.split(mil).join(""), decs);
    } else if (nc || np) {
      const sep = nc ? "," : ".", n = nc || np;
      if (n > 1) {
        if (!gruposOk(t, sep)) return { estado: "error", error: `Separadores mal colocados en el ${campo}.` };
        v = aNumero(t.split(sep).join(""), "");
      } else {
        const [ent, decs] = t.split(sep);
        if (sep === "." && decs.length === 3 && /^[1-9]\d{0,2}$/.test(ent)) {
          const comoDecimal = aNumero(ent, decs), comoMiles = aNumero(ent + decs, "");
          ambiguo = [comoDecimal, comoMiles];
        } else v = aNumero(ent, decs);
      }
    } else v = aNumero(t, "");
    if (ambiguo) return { estado: "ambiguo", opciones: ambiguo.map(x => ({ v: x, etiqueta: fmt(x, k) + unidad(k, sym) })),
      error: `«${texto}» admite dos lecturas: elige una o escríbelo sin ambigüedad (${fmt(ambiguo[0], k)} o ${fmt(ambiguo[1], k)}).` };
    if (v == null) return { estado: "error", error: `El ${campo} tiene demasiadas cifras o no es un número finito.` };
    if (!(v > 0)) return { estado: "error", error: `El ${campo} debe ser mayor que cero.` };
    return { estado: "ok", v };
  }

  /** Valor confirmado de un campo guardado. Formato nuevo: {n, txt}. Formato antiguo: cadena (se lee sin reescribirse).
      Devuelve {v|null, revisar: texto|null, legado: bool}. */
  function valorGuardado(x, kind, sym) {
    if (x == null || x === "") return { v: null, revisar: null, legado: false };
    if (typeof x === "object" && typeof x.n === "number")
      return Number.isFinite(x.n) && x.n > 0 ? { v: x.n, revisar: null, legado: false } : { v: null, revisar: "Valor guardado no válido.", legado: false };
    const r = leer(x, kind, sym);
    if (r.estado === "ok") return { v: r.v, revisar: null, legado: true };
    if (r.estado === "vacio") return { v: null, revisar: null, legado: true };
    return { v: null, legado: true, revisar: r.estado === "ambiguo"
      ? `Dato antiguo «${x}» ambiguo (${r.opciones.map(o => o.etiqueta).join(" o ")}): confírmalo antes de usarlo.`
      : `Dato antiguo «${x}» no se puede leer: corrígelo.` };
  }

  /** Lo que se guarda al confirmar un campo. */
  const confirmado = (v, txt) => ({ n: v, txt: String(txt) });

  const API = { leer, valorGuardado, confirmado, fmt, NOMBRE };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  raiz.CANT = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
