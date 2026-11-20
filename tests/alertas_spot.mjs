import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=fs.readFileSync(new URL('../web/index.html',import.meta.url),'utf8');
const lectura=html.slice(html.indexOf('function lecturaSpot'),html.indexOf('const liveB='));
const alertas=html.slice(html.indexOf('function stable('),html.indexOf('const BELL_ON'));
const frescoJs=html.slice(html.indexOf('fresco=o=>'),html.indexOf(';',html.indexOf('fresco=o=>')));
const ctx=vm.createContext({window:{}});   // la web publicada (window.ESTATICO) no avisa: aquí se prueba la del servidor
vm.runInContext(fs.readFileSync(new URL('../web/lib/spot.js',import.meta.url),'utf8'),ctx);
vm.runInContext(`
let now=1000,LASTT=now,AS={},PEND={},SEEN=null,ALERTS=true,PREF={al:{sig:true,niv:true,mov:true,pos:true,prev:true}},NEWS=[],CAL=[];
Date.now=()=>now;
const COINS={BTC:{}},${frescoJs};
const o={price:110,precio_t:now,z:{sup:[{lo:95,hi:99}],res:[{lo:120,hi:125}]},tm:{v:'E'},decision:{accion:'E',caduca:100000}};
const DATA={BTC:o},coins=()=>['BTC'],hasPos=()=>false,lab=x=>x,cool=()=>true,eur=x=>x,fp=x=>x,pc=x=>x;
const msgs=[],notify=x=>msgs.push(x);
const paso=(t,precio,fresco=true)=>{now=LASTT=t;o.price=precio;if(fresco)o.precio_t=t;o.decision.caduca=o.precio_t+120000;alerts()};
`+lectura+alertas,ctx);
// 1) el cambio de decisión spot ya no lo avisa el navegador: lo emite el servidor, con registro, para todas las monedas
for(const [t,v] of [[1000,'E'],[2000,'C'],[30000,'C'],[60000,'C']])
  vm.runInContext(`now=LASTT=${t};o.precio_t=now;o.tm.v=o.decision.accion='${v}';o.decision.caduca=now+120000;alerts()`,ctx);
assert.equal(vm.runInContext('msgs.length',ctx),0);
assert.equal(vm.runInContext('now=1e9;textoSpot("BTC",o)',ctx),'Pendiente de datos');
// 2) con datos vigentes, entrar en la Entrada 1 avisa
vm.runInContext('AS={};msgs.length=0;paso(100000,110);paso(101000,98)',ctx);
assert.equal(vm.runInContext('msgs.length',ctx),1);
// 3) con la cotización caducada no se evalúa: entrar en la zona sin dato vigente no avisa…
vm.runInContext('AS={};msgs.length=0;paso(200000,110);paso(330000,98,false);paso(400000,98,false)',ctx);
assert.equal(vm.runInContext('msgs.length',ctx),0);
// …y al volver un dato vigente se retoma en silencio (no avisa de un cruce que no vio)…
vm.runInContext('paso(401000,98)',ctx);
assert.equal(vm.runInContext('msgs.length',ctx),0);
// …pero un cruce nuevo con datos vigentes sí avisa
vm.runInContext('paso(402000,110);paso(403000,98)',ctx);
assert.equal(vm.runInContext('msgs.length',ctx),1);
console.log('Alertas y caducidad correctas');
