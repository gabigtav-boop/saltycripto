// Pruebas de aceptación de los bloques A (lectura coherente cuando faltan datos) y B (cantidades sin errores de escala).
// Datos SINTÉTICOS: comprueban el software, no son evidencia de mercado. Uso: node tests/estabilidad.mjs
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const leer = p => fs.readFileSync(new URL(p, import.meta.url), 'utf8');
const ctx = vm.createContext({ Intl });
vm.runInContext(leer('../web/lib/spot.js') + leer('../web/lib/cantidades.js'), ctx);
const { SPOT, CANT } = ctx;
let n = 0; const ok = (c, m) => { assert.ok(c, m); n++; };
const eq = (a, b, m) => { assert.deepEqual(JSON.parse(JSON.stringify(a ?? null)), b, m); n++; };  // objetos de otro contexto vm

/* ---------------- A ---------------- */
const T0 = 1_800_000_000_000;
const dec = (accion, pt, extra = {}) => ({ accion, motivo: 'm-' + accion, precio_t: pt, caduca: pt + 120000, zona: null, revisar: 'r', ...extra });
const act = (accion, pt) => ({ precio_t: pt, decision: dec(accion, pt) });
const COINS = { BTC: {}, ETH: {}, SOL: {}, PAXG: { cr: false } };
const sinPos = () => false, conPos = s => s === 'ETH';

// 119 / 120 / 121 s
eq(SPOT.lectura(act('E', T0), T0 + 119000).apto, true, '119 s apto');
eq(SPOT.lectura(act('E', T0), T0 + 120000).apto, true, '120 s apto (límite incluido)');
const l121 = SPOT.lectura(act('C', T0), T0 + 121000);
eq([l121.apto, l121.accion, l121.estado], [false, 'ND', 'caducada'], '121 s caducada');
eq(l121.anterior.accion, 'C', 'conserva la lectura anterior identificada como tal');
// hora futura
const lf = SPOT.lectura(act('C', T0 + 5000), T0);
eq([lf.apto, lf.estado], [false, 'futura'], 'una marca futura no es válida');
// cálculo obsoleto: el motor ya manda ND; se respeta su motivo y su lectura anterior
const lo = SPOT.lectura({ precio_t: T0, decision: dec('ND', T0, { motivo: 'Análisis incompleto o pendiente de renovar', anterior: { accion: 'E', motivo: 'x' } }) }, T0 + 1000);
eq([lo.apto, lo.motivo, lo.anterior.accion], [false, 'Análisis incompleto o pendiente de renovar', 'E']);
// sin decisión / sin objeto
eq(SPOT.lectura(undefined, T0).apto, false); eq(SPOT.lectura({ precio_t: T0 }, T0).accion, 'ND');

// textos según la posición; E (espera evaluada) ≠ ND (no evaluable)
eq([SPOT.texto('C', false), SPOT.texto('C', true), SPOT.texto('V', false), SPOT.texto('V', true)],
   ['Valorar entrada', 'Revisar ampliación', 'Evitar entrada', 'Revisar salida']);
ok(SPOT.texto('E', false) !== SPOT.texto('ND', false), 'E y ND se distinguen');

// resumen: 0, 1 y varios aptos
const DATA = {};
let R = SPOT.resumen(Object.keys(COINS), DATA, COINS, sinPos, T0);
eq([R.titular, R.cobertura], ['Decisión pendiente de datos', [0, 3]], '0 aptos');
ok(!/favorable/.test(R.titular + R.lineas.join(' ')), 'sin datos nunca dice «ninguna entrada favorable»');
DATA.BTC = act('E', T0);
R = SPOT.resumen(Object.keys(COINS), DATA, COINS, sinPos, T0 + 1000);
eq(R.cobertura, [1, 3]); ok(/entre los 1 evaluados/.test(R.titular), 'parcial: describe solo los evaluados');
ok(R.lineas.some(x => /Evaluados 1 de 3/.test(x) && /ETH/.test(x) && /SOL/.test(x)), 'dice cuántos entran y cuáles faltan');
DATA.ETH = act('V', T0); DATA.SOL = act('C', T0);
R = SPOT.resumen(Object.keys(COINS), DATA, COINS, sinPos, T0 + 1000);
eq([R.cobertura, R.titular], [[3, 3], 'Valorar entrada en SOL']);
ok(R.lineas.includes('Evitar entrada en ETH.'), 'V desde efectivo = evitar entrada');
R = SPOT.resumen(Object.keys(COINS), DATA, COINS, conPos, T0 + 1000);
ok(R.lineas.includes('Revisar salida en ETH.'), 'V con posición = revisar salida');
DATA.SOL = act('C', T0); R = SPOT.resumen(Object.keys(COINS), DATA, COINS, s => s === 'SOL', T0 + 1000);
eq(R.titular, 'Revisar ampliación en SOL', 'C con posición = revisar ampliación');

// pérdida del canal: no llegan mensajes y el reloj avanza → todo pasa a pendiente y la portada y el detalle coinciden
for (const t of [T0 + 60000, T0 + 120000, T0 + 121000, T0 + 600000 /* pestaña que vuelve del reposo */]) {
  const RS = SPOT.resumen(Object.keys(COINS), DATA, COINS, sinPos, t);
  for (const s of ['BTC', 'ETH', 'SOL']) eq(RS.lecturas[s].accion, SPOT.lectura(DATA[s], t).accion, `portada = detalle ${s} @${t - T0}`);
  eq(RS.titular === 'Decisión pendiente de datos', t - T0 > 120000, `titular coherente @${t - T0}`);
}
// reconexión: llega una cotización nueva
DATA.BTC = act('E', T0 + 700000);
R = SPOT.resumen(Object.keys(COINS), DATA, COINS, sinPos, T0 + 700500);
eq(R.cobertura, [1, 3], 'tras reconectar vuelve a contar');

// todo el catálogo cargado y solo PAXG vigente: el agregado cripto queda pendiente, PAXG se describe aparte
const D2 = { BTC: act('E', T0), ETH: act('E', T0), SOL: act('C', T0), PAXG: act('C', T0 + 500000) };
R = SPOT.resumen(Object.keys(COINS), D2, COINS, sinPos, T0 + 500500);
eq([R.titular, R.cobertura], ['Decisión pendiente de datos', [0, 3]], 'solo PAXG vigente');
eq(R.aparte.map(x => [x.s, x.apto, x.texto]), [['PAXG', true, 'Valorar entrada']], 'PAXG fuera del agregado');

/* ---------------- B ---------------- */
const L = (t, k = 'qty') => CANT.leer(t, k, 'BTC');
eq(L('0.0031'), { estado: 'ok', v: 0.0031 }); eq(L('0,0031'), { estado: 'ok', v: 0.0031 });
eq(L('1.500,25', 'eur'), { estado: 'ok', v: 1500.25 });
eq(CANT.fmt(1500.25, 'eur') + ' €', '1500,25 €'.replace('1500', '1.500'), 'presentación española');
const a = L('1.234');
eq(a.estado, 'ambiguo'); eq(a.opciones.map(o => o.v), [1.234, 1234]); ok(a.opciones.every(o => /BTC$/.test(o.etiqueta)), 'con unidad');
eq(L('1.234', 'eur').opciones.map(o => o.etiqueta), ['1,234 €', '1234,00 €'.replace('1234', '1.234')]);
for (const malo of ['1.25BTC', '1,2,3', '1..2', '1.2.3', '1,5.000', 'abc', '-3', '1e5', 'Infinity', 'NaN', '1,', '0', '12345678901234567'])
  eq(L(malo).estado, 'error', 'rechaza ' + malo);
ok(/Monedas|cantidad/.test(L('1.25BTC').error), 'dice qué campo corregir');
eq(L('1500').v, 1500); eq(L('1.234.567').v, 1234567); eq(L('0.234').v, 0.234); eq(L('1.5').v, 1.5); eq(L('1500.25').v, 1500.25);
eq(L('1,500.25', 'eur').v, 1500.25); eq(L(' 1.500,25 € ', 'eur').v, 1500.25); eq(L('').estado, 'vacio');
// precisión al recargar (JSON en localStorage)
const g = JSON.parse(JSON.stringify({ BTC: { qty: CANT.confirmado(0.0031, '0,0031') } }));
eq(CANT.valorGuardado(g.BTC.qty, 'qty').v, 0.0031);
eq(CANT.valorGuardado(CANT.confirmado(1234.56789012, '1234,56789012'), 'qty').v, 1234.56789012, 'más de mil SUI con fracción');
// dato antiguo (cadena): se lee sin reescribir; si es ambiguo queda para revisión
const viejo = CANT.valorGuardado('1.234', 'qty', 'SUI');
eq([viejo.v, viejo.legado], [null, true]); ok(/ambiguo/.test(viejo.revisar));
eq(CANT.valorGuardado('0,0031', 'qty').v, 0.0031); eq(CANT.valorGuardado('2500', 'eur').v, 2500);

// cableado de la cartera de index.html: un dato ambiguo bloquea el cálculo; uno a medio escribir no cambia la posición
const html = leer('../web/index.html');
const trozo = html.slice(html.indexOf('const TIPOP='), html.indexOf('function touchP'));
const c2 = vm.createContext({ Intl, CANT });
vm.runInContext('let POS={};const lab=s=>s;' + trozo + ';globalThis.F={pq,pinv,hasPos,pBloq,setPOS:x=>{POS=x}}', c2);
const F = c2.F;
F.setPOS({ SUI: { qty: '1.234', inv: '2500' } });
eq([F.hasPos('SUI'), F.pBloq('SUI')], [false, true], 'posición antigua ambigua: no se calcula ni se reinterpreta');
F.setPOS({ SUI: { qty: CANT.confirmado(1234, '1234'), inv: '2500' } });
eq([F.pq('SUI'), F.pinv('SUI'), F.hasPos('SUI')], [1234, 2500, true], 'más de mil SUI');
F.setPOS({ BTC: { qty: '0.0031', ent: CANT.confirmado(60000, '60.000,00') } });
eq([F.pq('BTC'), Math.round(F.pinv('BTC') * 100) / 100], [0.0031, 186], 'BTC fraccionario con precio de compra');
ok(html.includes('el.onchange=e=>{const t=e.target;if(t.dataset.p)pfConfirmar(s,t)}'), 'se confirma al salir del campo, no al escribir');
ok(!/el\.oninput=e=>\{const t=e\.target;if\(t\.dataset\.p\)\{\(POS\[s\]/.test(html), 'escribir ya no guarda el texto a medias');

console.log(`Estabilidad A/B: ${n} comprobaciones correctas`);
