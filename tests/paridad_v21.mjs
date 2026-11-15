// Ejecuta el motor ORIGINAL de la v21 (copiado tal cual de tests/ref/v21.html) sobre una instantánea fija
// del mercado y escribe el resultado en JSON. test_paridad.py compara este resultado con el motor Python.
// Uso: node tests/paridad_v21.mjs tests/fixtures/mercado.json
import fs from 'fs';
import vm from 'vm';
import path from 'path';
import {fileURLToPath} from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const L = fs.readFileSync(path.join(here, 'ref/v21.html'), 'utf8').split('\n');
const g = (a, b) => L.slice(a - 1, b).join('\n');
const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const code = [
  `Date.now=()=>${fx.now};`,
  g(121, 130),            // monedas, marcos temporales, utilidades y estado
  g(133, 133),            // vis()
  'const sched=()=>{};',
  // fuentes sustituidas por la instantánea
  `const K=async p=>{if(p.startsWith('Depth?pair='))return FX.depth[p.slice(11).split('&')[0]];if(p.startsWith('Ticker?pair='))return FX.ticker;throw new Error('sin fixture '+p)};
   const ohlc=async(key,min)=>{const d=FX.ohlc[key+'|'+min];if(!d)throw new Error('sin velas');const k=Object.keys(d).find(x=>x!=='last');return d[k].map(x=>({t:x[0]*1000,o:+x[1],h:+x[2],l:+x[3],c:+x[4],v:+x[6]}))};
   const tick=async key=>+TK[key].c[0];`,
  g(148, 251),            // indicadores, analyze, zones, tcore, sim, backtest, gs, bias, catAvg
  g(255, 263),            // bookSum
  g(266, 266),            // CGID
  g(273, 275),            // srcInfo, srcOk, refreshSrc
  g(277, 292),            // validate y utilidades
  g(331, 354),            // run, fin
  `(async()=>{
    PAIRS=FX.pairs;TK=FX.ticker;
    for(const s in FX.cbp)CBP[s]=+FX.cbp[s].data.amount;
    MK={};for(const s in CGID){const x=FX.mk.find(y=>y.id===CGID[s]);if(x)MK[s]={p:x.current_price,mc:x.market_cap,rk:x.market_cap_rank,ch:x.price_change_percentage_24h}}
    ORDER=Object.keys(COINS).sort((a,b)=>((MK[b]&&MK[b].mc)||0)-((MK[a]&&MK[a].mc)||0));
    CBC={};for(const s in FX.cbc){const rows=FX.cbc[s].map(x=>({t:x[0]*1000,l:x[1],h:x[2],o:x[3],c:x[4],v:x[5]})).sort((a,b)=>a.t-b.t),a=analyze(rows);if(a&&a.score!=null)CBC[s]={b:bias(a.score)}}
    const keys=['BTC',...ORDER.filter(k=>k!=='BTC')];
    for(const s of keys){const o=await run(s);DATA[s]=o;if(s==='BTC'&&o.bL)BTCL=o.bL}
    refreshSrc();
    const first=JSON.parse(JSON.stringify(proj()));
    for(const s of keys){const o=DATA[s];if(!o||!o.raw1d)continue;try{o.bt=await backtest(o.raw1d)}catch(e){o.bt=null}fin(o)}
    validate();
    const withBt=proj();
    for(const s in DATA){const o=DATA[s];if(!o||!o.d1)continue;const p=o.price*1.007;o.price=p;if(o.ref24)o.chg=(p/o.ref24-1)*100;o.z=zones(o.d1,p,o.book,o.res['1w']);fin(o)}
    OUT.r=JSON.stringify({order:ORDER,cbc:CBC,first,withBt,live:proj(),val:VAL});
  })().catch(e=>{OUT.err=String(e.stack||e)});
  function proj(){const o={};for(const s in DATA){const d=Object.assign({},DATA[s]);delete d.c;delete d.d1;delete d.raw1d;
    if(d.res){const r={};for(const k in d.res){const x=Object.assign({},d.res[k]);delete x.rows;delete x.E20;delete x.E50;r[k]=x}d.res=r}o[s]=d}return o}`,
].join('\n');

const OUT = {};
const ctx = vm.createContext({FX: fx, OUT, console, Intl, setTimeout});
vm.runInContext(code, ctx);
const wait = () => new Promise(r => setTimeout(r, 50));
while (!OUT.r && !OUT.err) await wait();
if (OUT.err) { console.error(OUT.err); process.exit(1); }
process.stdout.write(OUT.r);
