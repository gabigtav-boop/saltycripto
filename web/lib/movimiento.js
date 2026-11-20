/* SaltyCripto · movimiento de la esfera de la portada (ANALISIS_ANIMACION.md, 20 nov 2026).
   Solo matemáticas, sin dibujo: se prueba en node (tests/movimiento.mjs).

   muelle(m, objetivo, dt, respuesta, z)
     Oscilador amortiguado con solución EXACTA para un objetivo constante durante dt: el recorrido es el mismo a 30, 60 o
     120 fps y no se vuelve inestable con un paso grande (a diferencia de «x += (obj − x)·k» por fotograma o de integrar
     con Euler). m = {x, v}. respuesta = periodo de la oscilación sin amortiguar, en segundos (convención de Apple,
     «Designing Fluid Interfaces», WWDC 2018). z = razón de amortiguamiento: 1 llega lo antes posible sin pasarse;
     0,8 rebota una vez, apenas (≈1,5 %); por debajo de 0,6 el rebote ya se nota como muelle.
     Devuelve true si sigue en movimiento (se usa para decidir la cadencia de dibujo).
   suave(a, b, x)   smoothstep: 0 → 1 entre a y b con pendiente nula en los extremos (entradas y salidas sin escalón).
   eio(s)           ease-in-out cúbica: arranca y frena con suavidad (desplazamientos de A a B en pantalla).
   arco(P0..P3)     Bézier cúbica recorrida por LONGITUD DE ARCO: u = 0,5 es la mitad del trazo, no la mitad del parámetro
                    (con el parámetro, el punto corre en los tramos rectos y se frena en las curvas). */
(function (raiz) {
  "use strict";
  const clamp = (x, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
  const REPOSO_X = 1e-4, REPOSO_V = 1e-3;

  function muelle(m, obj, dt, resp, z) {
    if (!(dt > 0)) return Math.abs(m.x - obj) > REPOSO_X || Math.abs(m.v) > REPOSO_V;
    const w = 2 * Math.PI / resp, x0 = m.x - obj, v0 = m.v;
    if (z >= 1) {                       // crítico (z > 1 se trata como crítico: más lento sin ventaja visible)
      const e = Math.exp(-w * dt), k = (v0 + w * x0) * dt;
      m.x = obj + (x0 + k) * e;
      m.v = (v0 - w * k) * e;
    } else {                            // subamortiguado: x(t) = e^(−zωt)·(x0·cos ωd t + (v0 + zω x0)/ωd · sin ωd t)
      const wd = w * Math.sqrt(1 - z * z), e = Math.exp(-z * w * dt), c = Math.cos(wd * dt), s = Math.sin(wd * dt);
      m.x = obj + e * (x0 * c + (v0 + z * w * x0) / wd * s);
      m.v = e * (v0 * c - (z * w * v0 + w * w * x0) / wd * s);
    }
    if (Math.abs(m.x - obj) > REPOSO_X || Math.abs(m.v) > REPOSO_V) return true;
    m.x = obj; m.v = 0;                 // asentado: se fija exacto (sin deriva infinitesimal)
    return false;
  }
  const suave = (a, b, x) => { const s = clamp((x - a) / (b - a)); return s * s * (3 - 2 * s); };
  const eio = s => (s < 0.5 ? 4 * s * s * s : 1 - (-2 * s + 2) ** 3 / 2);
  const cub = (p0, p1, p2, p3, s) => {
    const q = 1 - s, a = q * q * q, b = 3 * q * q * s, c = 3 * q * s * s, d = s * s * s;
    return [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]];
  };
  function arco(P0, P1, P2, P3, n = 24) {
    const L = [0]; let p = P0, s = 0;
    for (let k = 1; k <= n; k++) { const q = cub(P0, P1, P2, P3, k / n); s += Math.hypot(q[0] - p[0], q[1] - p[1]); L.push(s); p = q; }
    const f = u => {
      const o = clamp(u) * s; let k = 1;
      while (k < n && L[k] < o) k++;
      const a = L[k - 1], b = L[k], r = b > a ? (o - a) / (b - a) : 0;
      return cub(P0, P1, P2, P3, (k - 1 + r) / n);
    };
    f.longitud = s;
    return f;
  }
  const API = { muelle, suave, eio, arco, cub, clamp };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  raiz.MOV = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
