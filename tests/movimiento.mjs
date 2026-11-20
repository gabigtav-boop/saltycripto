// Pruebas del movimiento de la esfera (web/lib/movimiento.js). Uso: node tests/movimiento.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const ctx = vm.createContext({});
vm.runInContext(fs.readFileSync(new URL('../web/lib/movimiento.js', import.meta.url), 'utf8'), ctx);
const { MOV } = ctx;
let n = 0; const ok = (c, m) => { assert.ok(c, m); n++; };
const cerca = (a, b, tol, m) => ok(Math.abs(a - b) <= tol, `${m}: ${a} frente a ${b}`);

// recorre un muelle de 0 a 1 durante `seg` segundos a `fps` y devuelve la trayectoria muestreada cada 1/30 s
function recorrer(fps, seg, resp, z, v0 = 0) {
  const m = { x: 0, v: v0 }, out = [], paso = 1 / fps, cada = Math.round(fps / 30);
  for (let k = 1; k <= seg * fps; k++) { MOV.muelle(m, 1, paso, resp, z); if (k % cada === 0) out.push(m.x); }
  return out;
}

// 1. independiente de los fotogramas por segundo: 30, 60 y 120 fps dan la misma trayectoria (solución exacta)
for (const [resp, z] of [[0.34, 1], [1.5, 0.82], [0.6, 0.4]]) {
  const a = recorrer(30, 3, resp, z), b = recorrer(60, 3, resp, z), c = recorrer(120, 3, resp, z);
  ok(a.every((x, i) => Math.abs(x - b[i]) < 1e-9 && Math.abs(x - c[i]) < 1e-9), `misma trayectoria a 30/60/120 fps (resp ${resp}, z ${z})`);
}
// la interpolación lineal por fotograma que había antes NO lo era: a 60 fps llegaba casi el doble de rápido que a 30
const lerp = (fps, seg) => { let x = 0; for (let k = 0; k < seg * fps; k++) x += (1 - x) * 0.04; return x; };
ok(Math.abs(lerp(30, 0.5) - lerp(60, 0.5)) > 0.15, 'el método anterior dependía de los fps (referencia)');

// 2. crítico (z = 1): nunca se pasa del objetivo y se asienta
const crit = recorrer(60, 3, 0.34, 1);
ok(Math.max(...crit) <= 1 + 1e-12, 'z = 1 no rebasa el objetivo');
ok(crit.at(-1) === 1, 'z = 1 se asienta exacto');
const t90 = crit.findIndex(x => x >= 0.9) / 30;
ok(t90 > 0.1 && t90 < 0.25, `resalte (0,34 s): 90 % en ${t90.toFixed(2)} s`);

// 3. subamortiguado: rebote acorde a la teoría, exp(−πz/√(1−z²))
for (const z of [0.82, 0.5]) {
  const tr = recorrer(120, 4, 1.5, z), pico = Math.max(...tr) - 1, teo = Math.exp(-Math.PI * z / Math.sqrt(1 - z * z));
  cerca(pico, teo, 0.004, `rebote con z = ${z}`);
}
ok(Math.max(...recorrer(60, 4, 1.5, 0.82)) - 1 < 0.02, 'nivel de luz (z = 0,82): rebote menor del 2 %');

// 4. paso grande (pestaña que vuelve): estable, sin explotar
const m = { x: 0, v: 0 };
MOV.muelle(m, 1, 5, 0.34, 1);
ok(m.x === 1 && m.v === 0, 'un paso de 5 s llega al objetivo sin oscilar');
const m2 = { x: 0, v: 0 };
for (let k = 0; k < 20; k++) MOV.muelle(m2, 1, 0.5, 0.2, 0.3);
ok(Number.isFinite(m2.x) && Math.abs(m2.x - 1) < 0.05, 'muelle rígido con paso grande: estable');

// 5. continuidad de velocidad: cambiar el objetivo a mitad del recorrido no produce un salto de posición
const m3 = { x: 0, v: 0 };
for (let k = 0; k < 6; k++) MOV.muelle(m3, 1, 1 / 60, 0.34, 1);
const antes = m3.x, vel = m3.v;
MOV.muelle(m3, 0, 1 / 60, 0.34, 1);
ok(m3.x > antes && vel > 0, 'al invertir el objetivo sigue avanzando un instante (conserva la inercia)');
ok(Math.abs(m3.x - antes) < 0.06, 'sin saltos al invertir');

// 6. reposo: devuelve false y fija el valor
const m4 = { x: 1, v: 0 };
ok(MOV.muelle(m4, 1, 1 / 60, 1, 1) === false, 'en reposo no pide fotogramas rápidos');
ok(MOV.muelle({ x: 0, v: 0 }, 1, 1 / 60, 1, 1) === true, 'en marcha sí');

// 7. curvas de tiempo
ok(MOV.suave(0, 1, 0) === 0 && MOV.suave(0, 1, 1) === 1 && MOV.suave(0, 1, 0.5) === 0.5, 'smoothstep: extremos y centro');
ok(MOV.suave(0, 1, -3) === 0 && MOV.suave(0, 1, 4) === 1, 'smoothstep: recortado');
const d0 = (MOV.eio(0.001) - MOV.eio(0)) / 0.001, d1 = (MOV.eio(1) - MOV.eio(0.999)) / 0.001;
ok(d0 < 0.01 && d1 < 0.01, 'ease-in-out: velocidad nula al salir y al llegar');
ok(MOV.eio(0.5) === 0.5 && MOV.eio(1) === 1, 'ease-in-out: simétrica');

// 8. longitud de arco: pasos iguales de u → distancias iguales sobre el trazo
const ruta = MOV.arco([0, 0], [300, 0], [300, 200], [400, 200]);
const pts = Array.from({ length: 11 }, (_, k) => ruta(k / 10)), seg = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]));
ok(Math.max(...seg) / Math.min(...seg) < 1.06, `velocidad uniforme sobre el trazo (${(Math.max(...seg) / Math.min(...seg)).toFixed(3)})`);
const ext = [ruta(0), ruta(1)];
ok(ext[0][0] === 0 && ext[0][1] === 0 && Math.abs(ext[1][0] - 400) < 1e-9 && Math.abs(ext[1][1] - 200) < 1e-9, 'extremos exactos');
// con el parámetro de la curva no lo era (referencia)
const pp = Array.from({ length: 11 }, (_, k) => MOV.cub([0, 0], [300, 0], [300, 200], [400, 200], k / 10));
const sp = pp.slice(1).map((p, i) => Math.hypot(p[0] - pp[i][0], p[1] - pp[i][1]));
ok(Math.max(...sp) / Math.min(...sp) > 1.3, 'el parámetro de Bézier no da velocidad uniforme (referencia)');

console.log(`Movimiento: ${n} comprobaciones correctas`);
