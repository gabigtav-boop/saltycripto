"""Analizador fundamental: noticias de cripto y macro en tiempo real, calendario económico
y traslado al mercado (a qué activos afecta, en qué dirección, con qué fuerza y si el precio lo confirma).

El análisis es por reglas transparentes (temas y vocabulario), no una caja negra: cada noticia
muestra qué palabras activaron su lectura. La reacción real del precio tras la noticia se mide
con velas de 1 minuto para comprobar si el mercado la confirma."""
import html
import json
import math
import pathlib
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import fuentes

FEEDS = [
    # nombre, url, tipo, peso de la fuente
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "cripto", 1.0),
    ("Cointelegraph", "https://cointelegraph.com/rss", "cripto", 1.0),
    ("The Block", "https://www.theblock.co/rss.xml", "cripto", 1.0),
    ("Decrypt", "https://decrypt.co/feed", "cripto", .9),
    ("CryptoSlate", "https://cryptoslate.com/feed/", "cripto", .8),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/", "cripto", .8),
    ("Investing.com", "https://www.investing.com/rss/news_301.rss", "cripto", .8),
    ("Google Noticias", "https://news.google.com/rss/search?q=bitcoin+OR+criptomonedas+OR+ethereum&hl=es&gl=ES&ceid=ES:es", "cripto", .6),
    ("Reserva Federal", "https://www.federalreserve.gov/feeds/press_all.xml", "macro", 1.3),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "macro", 1.0),
    ("CNBC Economía", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "macro", 1.0),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "macro", .9),
    ("FXStreet", "https://www.fxstreet.com/rss/news", "macro", .9),
    ("BBC Economía", "https://feeds.bbci.co.uk/news/business/rss.xml", "macro", .8),
]
CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CAL_CACHE = pathlib.Path(__file__).resolve().parent.parent / "datos" / "calendario.json"

COIN_WORDS = {
    "BTC": [r"bitcoin", r"btc", r"satoshi"],
    "ETH": [r"ethereum", r"ether", r"eth", r"vitalik"],
    "XRP": [r"xrp", r"ripple"],
    "SOL": [r"solana", r"(?-i:\$?SOL)"],          # "sol" (el astro) y "hype" (exageración) son palabras corrientes:
    "HYPE": [r"hyperliquid", r"(?-i:\$?HYPE)"],   # el ticker solo cuenta escrito en mayúsculas
    "SUI": [r"(?-i:\$?SUI|Sui)"],
    "ZEC": [r"zcash", r"zec"],
    "PAXG": [r"gold", r"oro", r"paxg", r"bullion", r"xau"],
}
COIN_RX = {s: re.compile(r"\b(" + "|".join(w) + r")\b", re.I) for s, w in COIN_WORDS.items()}
CRYPTO_RX = re.compile(r"\b(crypto\w*|cripto\w*|bitcoin|blockchain|stablecoin\w*|defi|altcoin\w*|token\w*|web3|exchange[s]?|binance|coinbase|kraken)\b", re.I)

# tema: (nombre, peso, patrón, vocabulario positivo, vocabulario negativo, efecto en oro: +1 igual, -1 inverso, 0 sin efecto)
TOPICS = [
    ("Política monetaria", 3.0, r"\b(fed|fomc|powell|federal reserve|reserva federal|interest rates?|rate (cut|hike)s?|tipos de inter[eé]s|ecb|bce|lagarde|central bank|banco central|monetary policy)\b",
     r"\b(cuts?|cutting|lower(s|ed)? rates|eas(e|es|ing)|dovish|pause[sd]? hikes?|bajada de tipos|recorte|rebaja|stimulus|liquidity injection|qe)\b",
     r"\b(hikes?|hiking|raise[sd]? rates|hawkish|tighten\w*|higher for longer|subida de tipos|endurec\w*)\b", 1),
    ("Inflación", 2.5, r"\b(inflation|cpi|pce|ppi|inflaci[oó]n|ipc|prices index)\b",
     r"\b(cool\w*|eas(e|es|ed|ing)|slow\w*|below (expectations|forecast)|lower than expected|moderat\w*|se modera|baja)\b",
     r"\b(hot(ter)?|accelerat\w*|above (expectations|forecast)|higher than expected|surg\w*|jump\w*|repunta|sube)\b", 1),
    ("Empleo y crecimiento", 2.0, r"\b(jobs?|payrolls?|nfp|unemployment|jobless|gdp|pib|recession|recesi[oó]n|empleo|paro|economy|econom[ií]a)\b",
     r"\b(beat\w*|strong(er)?|grow\w*|expan\w*|resilient|crece|sólid\w*)\b",
     r"\b(recession\w*|recesi[oó]n|contract\w*|weak\w*|miss\w*|layoffs?|despidos|slowdown|shrink\w*)\b", -1),
    ("Geopolítica", 2.5, r"\b(war|guerra|missile|attack\w*|ataque|sanction\w*|sanciones|invasion|conflict|conflicto|iran|israel|russia|rusia|ukraine|ucrania|taiwan|nato|otan|military|strikes?)\b",
     r"\b(ceasefire|truce|peace|paz|tregua|de-?escalat\w*|agreement|acuerdo)\b",
     r"\b(escalat\w*|attack\w*|ataque\w*|missile\w*|invad\w*|strike[sd]?|bomb\w*|tension\w*|threat\w*|amenaza\w*)\b", -1),
    ("Aranceles y comercio", 2.0, r"\b(tariffs?|aranceles?|trade war|guerra comercial|trade deal|export controls?)\b",
     r"\b(deal|agreement|acuerdo|truce|lift\w*|remov\w*|exempt\w*|pause[sd]?)\b",
     r"\b(impos\w*|rais\w*|new tariffs?|escalat\w*|retaliat\w*|threat\w*|sube|impone)\b", -1),
    ("Regulación", 2.5, r"\b(sec|cftc|regulat\w*|regulador\w*|regulaci[oó]n|lawsuit|demanda|congress|senate|senado|bill|ley|mica|legislation|gensler|atkins|clarity act|genius act|ban(s|ned)?|prohib\w*|court|judge|tribunal)\b",
     r"\b(approv\w*|aprueba\w*|clarity|framework|pass(es|ed)|dismiss\w*|drops? (case|lawsuit)|win\w*|favorable|legaliz\w*|green light|friendly)\b",
     r"\b(sues?|sued|charg\w*|crackdown|ban(s|ned)?|prohib\w*|fine[sd]?|multa\w*|reject\w*|rechaza\w*|investigat\w*|enforcement|subpoena)\b", 0),
    ("ETF e institucionales", 2.5, r"\b(etfs?|inflows?|outflows?|blackrock|fidelity|grayscale|ishares|ibit|strategy|microstrategy|saylor|treasur(y|ies) compan\w*|institutional|institucional\w*|fondos?)\b",
     r"\b(inflows?|entradas|buys?|bought|compra\w*|accumulat\w*|acumula\w*|adds?|record|approv\w*|launch\w*|files? for)\b",
     r"\b(outflows?|salidas|sells?|sold|vende\w*|dump\w*|redemptions?|withdraw\w*)\b", 0),
    ("Seguridad", 3.0, r"\b(hack\w*|exploit\w*|drain\w*|stolen|robo|robad\w*|breach\w*|vulnerab\w*|scam\w*|estafa\w*|rug ?pull|attacker)\b",
     r"\b(recover\w*|recupera\w*|return(s|ed) funds|frozen|patched)\b",
     r"\b(hack\w*|exploit\w*|drain\w*|stolen|robo|breach\w*|scam\w*|estafa\w*|loses?|lost)\b", 0),
    ("Adopción y tecnología", 1.5, r"\b(upgrade|mainnet|launch\w*|lanza\w*|partnership|alianza|integrat\w*|adopt\w*|listing|lists|payments?|pagos|network|red|protocol|tokeniz\w*)\b",
     r"\b(launch\w*|lanza\w*|partner\w*|integrat\w*|adopt\w*|list(s|ing|ed)|upgrade[sd]?|expan\w*|record|milestone|hito)\b",
     r"\b(delay\w*|retras\w*|outage|halt\w*|caída del servicio|delist\w*|bug|shut\w* down|cierra)\b", 0),
    ("Dólar y bonos", 2.0, r"\b(dollar|d[oó]lar|dxy|treasury yields?|bond yields?|rendimientos?|bonos?|10-year)\b",
     r"\b(weak\w*|fall\w*|drop\w*|slid\w*|decline\w*|cae\w*|debilita\w*)\b",
     r"\b(strength\w*|strong\w*|ris(e|es|ing)|climb\w*|jump\w*|surg\w*|sube\w*|fortalece\w*)\b", 1),
    ("Bolsa y apetito de riesgo", 1.5, r"\b(stocks?|nasdaq|s&p|wall street|dow|equities|acciones|bolsa|risk assets?)\b",
     r"\b(rall\w*|record high|surg\w*|gain\w*|climb\w*|sube\w*|máximos)\b",
     r"\b(sell-?off|plung\w*|tumbl\w*|slump\w*|fall\w*|drop\w*|cae\w*|desplom\w*|rout)\b", -1),
    ("Apalancamiento", 1.5, r"\b(liquidat\w*|liquidaci\w*|leverage\w*|apalanca\w*|short squeeze|open interest|funding)\b",
     r"\b(short squeeze|shorts? liquidated|short liquidations)\b",
     r"\b(longs? liquidated|long liquidations|liquidations? (top|hit|surge)|deleverag\w*|cascade)\b", 0),
]
TOPIC_RX = [(n, w, re.compile(p, re.I), re.compile(pos, re.I), re.compile(neg, re.I), g) for n, w, p, pos, neg, g in TOPICS]
CRYPTO_ONLY = {"Regulación", "ETF e institucionales", "Seguridad", "Adopción y tecnología", "Apalancamiento"}
NOT_BTC = re.compile(r"\bbitcoin (cash|sv|gold)\b|\bbch\b", re.I)
GEN_POS = re.compile(r"\b(surg\w*|soar\w*|rall\w*|jump\w*|climb\w*|gain\w*|record high|all-time high|ath|rebound\w*|recover\w*|breaks? above|outperform\w*|sube\w*|dispara\w*|repunta\w*|máximo\w*|alcista\w*|optimis\w*|boost\w*|upgrade\w*|approv\w*)\b", re.I)
GEN_NEG = re.compile(r"\b(plung\w*|crash\w*|tumbl\w*|slump\w*|sink\w*|fall\w*|drop\w*|slid\w*|declin\w*|sell-?off|wiped out|picada|en picado|lunes negro|lowest|dump\w*|breaks? below|fear\w*|panic\w*|cae\w*|desplom\w*|hund\w*|bajista\w*|pierde\w*|caída\w*|warn\w*|risk\w*|concern\w*|worr\w*|downgrade\w*)\b", re.I)
NEGATORS = re.compile(r"\b(no|not|n't|without|sin|fails? to|unlikely|denies|niega)\s+(\w+\s+){0,2}$", re.I)
PRICE_ONLY = re.compile(r"\b(price (prediction|analysis)|predicci[oó]n|technical analysis|análisis técnico|chart)\b", re.I)
STOP = set("the a an of to in on for and or is are be with as at by from this that it its will has have new after over into than more says said la el los las de del y en un una por para con que se su al".split())


def _txt(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    try:
        return int(parsedate_to_datetime(s).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:  # noqa: BLE001
        return None


def parse_feed(xml_bytes, fuente):
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        get = {c.tag.split("}")[-1]: c for c in it}
        title = _txt(get["title"].text if "title" in get else "")
        link = ""
        if "link" in get:
            link = (get["link"].text or get["link"].get("href") or "").strip()
        desc = ""
        for k in ("description", "summary", "content"):
            if k in get and get[k].text:
                desc = _txt(get[k].text)[:400]
                break
        d = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in get and get[k].text:
                d = parse_date(get[k].text.strip())
                if d:
                    break
        if title and d:
            if fuente == "Google Noticias" and " - " in title:
                title, src = title.rsplit(" - ", 1)
                fuente_i = f"{src.strip()} (Google)"
            else:
                fuente_i = fuente
            items.append({"titulo": title, "link": link, "desc": desc, "t": d, "fuente": fuente_i})
    return items


def _hits(rx, text):
    out = []
    for m in rx.finditer(text):
        neg = bool(NEGATORS.search(text[max(0, m.start() - 40):m.start()]))
        out.append((m.group(0).lower(), -1 if neg else 1))
    return out


def analizar(item, tipo="cripto", peso=1.0):
    """Clasifica una noticia: temas, activos afectados, dirección (-1..1), fuerza (0..10) y motivos."""
    tt = item["titulo"]
    desc = item.get("desc", "")
    full = tt + ". " + desc
    clean_t, clean_d = NOT_BTC.sub(" ", tt), NOT_BTC.sub(" ", desc)
    coins = [s for s, rx in COIN_RX.items() if rx.search(clean_t)] or [s for s, rx in COIN_RX.items() if rx.search(clean_d)][:2]
    # "sol"/"hype"/"sui"/"gold" son palabras corrientes: solo cuentan si la noticia es de cripto o mercados
    is_crypto = tipo == "cripto" or bool(CRYPTO_RX.search(full)) or any(c in coins for c in ("BTC", "ETH", "XRP", "ZEC"))
    if not is_crypto:
        coins = [c for c in coins if c == "PAXG"]
    temas, pos_w, neg_w, motivos, oro = [], 0.0, 0.0, [], 0.0
    for name, w, rx, prx, nrx, g in TOPIC_RX:
        if not rx.search(full) or (name in CRYPTO_ONLY and not is_crypto):
            continue
        p = sum(s for _, s in _hits(prx, tt)) * 2 + sum(s for _, s in _hits(prx, item.get("desc", "")))
        n = sum(s for _, s in _hits(nrx, tt)) * 2 + sum(s for _, s in _hits(nrx, item.get("desc", "")))
        temas.append({"tema": name, "peso": w})
        if p or n:
            d = (p - n) / (abs(p) + abs(n))
            pos_w += max(d, 0) * w
            neg_w += max(-d, 0) * w
            oro += d * g * w
            motivos += [h for h, _ in _hits(prx, full)][:2] + [h for h, _ in _hits(nrx, full)][:2]
    gp = _hits(GEN_POS, tt)
    gn = _hits(GEN_NEG, tt)
    gs = sum(s for _, s in gp) - sum(s for _, s in gn)
    if gs:
        g = 1.0 if not temas else .5
        pos_w += max(gs, 0) * g
        neg_w += max(-gs, 0) * g
        motivos += [h for h, _ in gp + gn][:3]
    tot = pos_w + neg_w
    dir_ = (pos_w - neg_w) / tot if tot else 0.0
    base = max([t["peso"] for t in temas], default=1.0)
    if PRICE_ONLY.search(full) and base <= 1.5:
        base *= .6
    fuerza = min(10.0, base * peso * (.4 + .6 * min(1, tot / 3)) * 2)
    afecta = {}
    if not temas and not coins and not is_crypto:
        fuerza = 0.0  # sin relación con los mercados que seguimos
    if coins:
        for c in coins:
            afecta[c] = dir_
    elif is_crypto or temas:
        afecta["MERCADO"] = dir_
        if is_crypto and temas and all(t["tema"] in ("Seguridad", "Adopción y tecnología") for t in temas):
            fuerza *= .5  # suceso de un proyecto concreto: efecto limitado en el mercado
    if "PAXG" not in afecta and oro and temas:
        afecta["PAXG"] = max(-1, min(1, oro / max(base, 1)))
    if "PAXG" in coins and not is_crypto:
        afecta.pop("MERCADO", None)
    nivel = "Alto" if fuerza >= 6 else "Medio" if fuerza >= 3 else "Bajo"
    lect = "Positiva" if dir_ >= .2 else "Negativa" if dir_ <= -.2 else "Neutral"
    return {"temas": [t["tema"] for t in temas], "monedas": coins, "dir": dir_, "fuerza": round(fuerza, 2),
            "nivel": nivel, "lectura": lect, "afecta": afecta, "motivos": list(dict.fromkeys(motivos))[:5],
            "es_cripto": is_crypto}


def _tokens(t):
    return {w for w in re.findall(r"[a-záéíóúñ0-9]+", t.lower()) if w not in STOP and len(w) > 2}


def agrupar(items):
    """Une la misma historia contada por varios medios (cobertura)."""
    grupos = []
    for it in sorted(items, key=lambda x: x["t"]):
        tk = _tokens(it["titulo"])
        best = None
        for g in grupos:
            if abs(it["t"] - g["t"]) > 36e5 * 12:
                continue
            j = len(tk & g["_tk"]) / max(1, len(tk | g["_tk"]))
            if j >= .45 and (not best or j > best[0]):
                best = (j, g)
        if best:
            g = best[1]
            g["fuentes"] = sorted(set(g["fuentes"]) | {it["fuente"]})
            g["_tk"] |= tk
        else:
            it = dict(it, fuentes=[it["fuente"]], _tk=tk)
            grupos.append(it)
    return grupos


def precio_en(velas, t):
    """Precio de cierre de la vela que contiene t (velas ordenadas, dicts con t y c)."""
    if not velas or t < velas[0]["t"]:
        return None
    lo, hi = 0, len(velas) - 1
    while lo < hi:
        m = (lo + hi + 1) // 2
        if velas[m]["t"] <= t:
            lo = m
        else:
            hi = m - 1
    return velas[lo]["c"]


def reaccion(n, velas1m, velas1h, ahora):
    """Mueve del precio desde la publicación: a 60 min y hasta ahora. Confirma o contradice la lectura."""
    v = velas1m if velas1m and n["t"] >= velas1m[0]["t"] else velas1h
    p0 = precio_en(v, n["t"])
    if not p0:
        return None
    p60 = precio_en(v, n["t"] + 36e5) if ahora - n["t"] >= 36e5 else None
    pn = v[-1]["c"]
    r60 = (p60 / p0 - 1) * 100 if p60 else None
    rn = (pn / p0 - 1) * 100
    ref = r60 if r60 is not None else rn
    conf = None
    if abs(n["dir"]) >= .2 and (r60 is not None or ahora - n["t"] >= 9e5):
        if abs(ref) < .25:
            conf = "Sin reacción"
        elif (ref > 0) == (n["dir"] > 0):
            conf = "Confirmada"
        else:
            conf = "Contradicha"
    return {"r60": r60, "ahora": rn, "conf": conf}


CAL_ES = {
    "CPI m/m": "IPC mensual", "CPI y/y": "IPC anual", "Core CPI m/m": "IPC subyacente mensual", "Non-Farm Employment Change": "Nóminas no agrícolas",
    "Unemployment Rate": "Tasa de paro", "Federal Funds Rate": "Tipos de la Fed", "FOMC Statement": "Comunicado del FOMC",
    "FOMC Press Conference": "Rueda de prensa del FOMC", "FOMC Meeting Minutes": "Actas del FOMC", "Core PCE Price Index m/m": "PCE subyacente mensual",
    "Advance GDP q/q": "PIB adelantado", "Prelim GDP q/q": "PIB preliminar", "Final GDP q/q": "PIB final", "Retail Sales m/m": "Ventas minoristas",
    "Core Retail Sales m/m": "Ventas minoristas subyacentes", "PPI m/m": "IPP mensual", "Core PPI m/m": "IPP subyacente", "Unemployment Claims": "Peticiones de paro",
    "ISM Manufacturing PMI": "PMI manufacturero ISM", "ISM Services PMI": "PMI servicios ISM", "Main Refinancing Rate": "Tipos del BCE",
    "Monetary Policy Statement": "Comunicado de política monetaria", "JOLTS Job Openings": "Ofertas de empleo JOLTS", "Prelim UoM Consumer Sentiment": "Confianza del consumidor UoM",
    "Fed Chair Powell Speaks": "Habla Powell (Fed)", "ECB President Lagarde Speaks": "Habla Lagarde (BCE)", "Average Hourly Earnings m/m": "Salario por hora",
}


def traducir_evento(t):
    """Nombre del evento en español: tabla de los habituales y, para comparecencias, «Habla …»."""
    if t in CAL_ES:
        return CAL_ES[t]
    m = re.match(r"^(.*?)\s+Speaks$", t)
    if m:
        quien = m.group(1)
        for a, b in (("Fed Chairman", "presidente de la Fed"), ("Fed Chair", "presidencia de la Fed"), ("ECB President", "presidencia del BCE"),
                     ("BOE Gov", "gobernador del BoE"), ("BOJ Gov", "gobernador del BoJ"), ("FOMC Member", "miembro del FOMC"), ("President", "presidente")):
            if quien.startswith(a):
                nombre = quien[len(a):].strip()
                return f"Habla {nombre} ({b})" if nombre else f"Habla {b}"
        return f"Habla {quien}"
    return t


def calendario(raw, ahora):
    out = []
    for e in raw or []:
        if e.get("impact") not in ("High", "Medium") or e.get("country") not in ("USD", "EUR", "CNY", "JPY", "GBP"):
            continue
        t = parse_date(e.get("date"))
        if not t or t < ahora - 7 * 864e5 or t > ahora + 7 * 864e5:
            continue
        rel = 3 if e["country"] == "USD" and e["impact"] == "High" else 2 if e["impact"] == "High" else 1
        out.append({"t": t, "titulo": traducir_evento(e["title"]), "orig": e["title"], "pais": e["country"],
                    "impacto": "Alto" if e["impact"] == "High" else "Medio", "prevision": e.get("forecast") or "",
                    "anterior": e.get("previous") or "", "actual": e.get("actual") or "", "rel": rel})
    out.sort(key=lambda x: x["t"])
    return out


class Fundamental:
    """Descarga y analiza noticias periódicamente; calcula el pulso fundamental por activo."""

    def __init__(self):
        self.items = {}       # clave -> noticia analizada
        self.estado = {}      # fuente -> {ok, n, t, error}
        self._cond = {}       # fuente -> {etag, mod} para peticiones condicionales
        self.cal = []
        self.cal_ok = None
        self.cal_t = None
        self._cal_try = 0
        self.t = 0

    def _feed(self, f):
        nombre, url, tipo, peso = f
        try:
            # petición condicional: si el medio no ha cambiado responde 304 y no se descarga nada
            h = {"User-Agent": "Mozilla/5.0 (Macintosh) saltycripto"}
            cond = self._cond.get(nombre, {})
            if cond.get("etag"):
                h["If-None-Match"] = cond["etag"]
            if cond.get("mod"):
                h["If-Modified-Since"] = cond["mod"]
            r = fuentes._session.get(url, timeout=12, headers=h)
            if r.status_code == 304:
                prev = self.estado.get(nombre, {})
                self.estado[nombre] = {"ok": True, "n": prev.get("n", 0), "t": int(time.time() * 1000), "error": None}
                return []
            r.raise_for_status()
            self._cond[nombre] = {"etag": r.headers.get("ETag"), "mod": r.headers.get("Last-Modified")}
            its = parse_feed(r.content, nombre)
            for it in its:
                it.update(tipo=tipo, peso=peso)
            self.estado[nombre] = {"ok": True, "n": len(its), "t": int(time.time() * 1000), "error": None}
            return its
        except Exception as e:  # noqa: BLE001
            prev = self.estado.get(nombre, {})
            self.estado[nombre] = {"ok": False, "n": prev.get("n", 0), "t": prev.get("t"), "error": str(e)[:100]}
            return []

    def actualizar(self):
        ahora = time.time() * 1000
        with ThreadPoolExecutor(8) as ex:
            lotes = list(ex.map(self._feed, FEEDS))
        nuevos = 0
        for its in lotes:
            for it in its:
                if it["t"] < ahora - 2 * 864e5 or it["t"] > ahora + 6e5:
                    continue
                k = re.sub(r"\W+", "", it["titulo"].lower())[:90]
                if k in self.items:
                    continue
                it["k"] = k
                it.update(analizar(it, it["tipo"], it["peso"]))
                self.items[k] = it
                nuevos += 1
        for k in [k for k, v in self.items.items() if v["t"] < ahora - 2 * 864e5]:
            del self.items[k]
        self.actualizar_calendario(ahora)
        self.t = ahora
        return nuevos

    def actualizar_calendario(self, ahora):
        """El calendario solo cambia cada hora y su servidor bloquea a quien lo pide más a menudo:
        se pide como mucho una vez por hora y se guarda en disco para sobrevivir a reinicios."""
        raw, t_raw = None, 0
        try:
            c = json.loads(CAL_CACHE.read_text())
            raw, t_raw = c["raw"], c["t"]
        except Exception:  # noqa: BLE001
            pass
        if time.time() - t_raw >= 3600 and time.time() - self._cal_try >= 900:
            self._cal_try = time.time()
            try:
                r = fuentes._session.get(CALENDAR, timeout=12)
                r.raise_for_status()
                raw, t_raw = r.json(), time.time()
                CAL_CACHE.parent.mkdir(exist_ok=True)
                CAL_CACHE.write_text(json.dumps({"t": t_raw, "raw": raw}))
            except Exception:  # noqa: BLE001
                pass
        if raw is not None:
            self.cal = calendario(raw, ahora)
        self.cal_ok = raw is not None and time.time() - t_raw < 8 * 3600
        self.cal_t = int(t_raw * 1000) if t_raw else None

    def lista(self, velas1m, velas1h, ahora=None, n=80):
        """Historias agrupadas, más recientes primero, con reacción real del precio."""
        ahora = ahora or time.time() * 1000
        gs = agrupar(self.items.values())
        out = []
        for g in [g for g in sorted(gs, key=lambda x: -x["t"]) if g["afecta"]][:n]:
            cob = len(g["fuentes"])
            f = min(10.0, g["fuerza"] * (1 + .2 * (cob - 1)))
            x = {k: v for k, v in g.items() if not k.startswith("_") and k not in ("k",)}
            x["fuerza"] = round(f, 2)
            x["nivel"] = "Alto" if f >= 6 else "Medio" if f >= 3 else "Bajo"
            x["cobertura"] = cob
            ref = next(iter(g["afecta"]), None)
            sym = "BTC" if ref in (None, "MERCADO") else ref
            x["ref"] = sym
            x["reac"] = reaccion(g, velas1m.get(sym), velas1h.get(sym), ahora) if g["afecta"] else None
            x["horiz"] = horizonte(x["temas"])
            x["efecto"] = efecto(x)
            out.append(x)
        return out

    @staticmethod
    def pulso(lista, ahora=None):
        """Pulso fundamental -100..100 por activo y para el mercado (decae con vida media de 8 h)."""
        ahora = ahora or time.time() * 1000
        acc = {}
        for n in lista:
            age = max(0, (ahora - n["t"]) / 36e5)
            w = n["fuerza"] * math.exp(-age / 8 * math.log(2))
            for a, d in n["afecta"].items():
                s, ww, c = acc.get(a, (0.0, 0.0, 0))
                acc[a] = (s + d * w, ww + w, c + 1)
        out = {}
        for a, (s, ww, c) in acc.items():
            if ww <= 0:
                continue
            sc = max(-100, min(100, s / max(ww, 6) * 100))
            out[a] = {"score": sc, "n": c, "lectura": "Positivo" if sc >= 15 else "Negativo" if sc <= -15 else "Neutral"}
        return out


HORIZ = {"Política monetaria": "días", "Inflación": "días", "Empleo y crecimiento": "días", "Geopolítica": "horas a días",
         "Aranceles y comercio": "días", "Regulación": "días a semanas", "ETF e institucionales": "días", "Seguridad": "horas",
         "Adopción y tecnología": "días", "Dólar y bonos": "días", "Bolsa y apetito de riesgo": "horas", "Apalancamiento": "horas"}
NOMBRE = {"MERCADO": "el mercado cripto", "PAXG": "el oro"}


def horizonte(temas):
    return HORIZ.get(temas[0], "horas") if temas else "horas"


def efecto(n):
    """Frase corta: cómo puede afectar la noticia al gráfico."""
    if not n["afecta"] or abs(n["dir"]) < .2 or n["fuerza"] < 1:
        return "Sin efecto claro en el precio"
    quien = [NOMBRE.get(a, a) for a in n["afecta"]][:2]
    tipo = "Presión bajista" if n["dir"] < 0 else "Impulso alcista"
    grado = " fuerte" if n["fuerza"] >= 6 else "" if n["fuerza"] >= 3 else " leve"
    return f"{tipo}{grado} sobre {' y '.join(quien)} · {n['horiz']}"


def resumen_impacto(lista, activo, pul, ahora=None):
    """Resumen de cómo las noticias de 24 h pueden mover el gráfico de un activo."""
    ahora = ahora or time.time() * 1000
    rel = [n for n in lista if ahora - n["t"] <= 864e5 and (activo in n["afecta"] or
           (activo not in ("MERCADO", "PAXG") and "MERCADO" in n["afecta"]))]
    p = pul.get(activo)
    if not rel or not p:
        return {"activo": activo, "n": 0, "texto": "Sin noticias relevantes en las últimas 24 h.", "efecto": "Sin efecto fundamental claro",
                "dir": "neutral", "score": 0, "temas": [], "confirmadas": 0, "contradichas": 0}
    tw = {}
    for n in rel:
        w = n["fuerza"] * math.exp(-max(0, ahora - n["t"]) / 36e5 / 8 * math.log(2))
        d = n["afecta"].get(activo, n["afecta"].get("MERCADO", 0))
        for t in n["temas"] or ["Movimiento de precio"]:
            a, b = tw.get(t, (0.0, 0.0))
            tw[t] = (a + d * w, b + w)
    temas = sorted(({"tema": t, "dir": a / b if b else 0, "peso": b} for t, (a, b) in tw.items()), key=lambda x: -x["peso"])[:3]
    sc = p["score"]
    dirc = "bajista" if sc <= -15 else "alcista" if sc >= 15 else "neutral"
    conf = sum(1 for n in rel if n.get("reac") and n["reac"].get("conf") == "Confirmada")
    contra = sum(1 for n in rel if n.get("reac") and n["reac"].get("conf") == "Contradicha")
    nom = NOMBRE.get(activo, activo)
    tono = {"bajista": "Noticias negativas", "alcista": "Noticias positivas", "neutral": "Noticias mixtas"}[dirc]
    tt = [f"{x['tema'].lower()} ({'en contra' if x['dir'] < -.15 else 'a favor' if x['dir'] > .15 else 'neutro'})" for x in temas]
    hz = HORIZ.get(temas[0]["tema"], "horas") if temas else "horas"
    texto = f"{tono} para {nom} en 24 h ({len(rel)}). Pesan: {', '.join(tt)}."
    if conf + contra:
        texto += f" El precio confirmó {conf} de {conf + contra} con reacción clara."
    ef = {"bajista": f"Presión bajista esperada en el gráfico · {hz}", "alcista": f"Impulso alcista esperado en el gráfico · {hz}",
          "neutral": "Sin presión clara en el gráfico"}[dirc]
    return {"activo": activo, "n": len(rel), "texto": texto, "efecto": ef, "dir": dirc, "score": sc, "temas": temas,
            "confirmadas": conf, "contradichas": contra}
