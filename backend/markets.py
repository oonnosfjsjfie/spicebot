"""Live market data for SpiceBot: spice prices (India and world), global commodities and EHEDG news.

Sources
- Spices Board of India, "Current Market Price of Key Spices" page (official, no key needed).
- Gemini with Google Search, for spice prices the Spices Board page doesn't list and for world prices.
  Every AI-found price comes with the web sources Gemini used.
- IMF Primary Commodity Prices through FRED (St. Louis Fed), monthly, no key needed.
- FRED DEXINUS for the rupee/dollar rate.

Everything is cached and refreshed in the background, so pages stay fast and the Gemini quota lasts.
"""
import base64
import csv
import html
import io
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = "Mozilla/5.0 (compatible; SpiceBot/1.0; +https://spicebot.onrender.com)"
SPICES_BOARD = "https://indianspices.com/marketing/price/domestic/current-market-price"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def http_bytes(url, timeout=40, limit=15_000_000, referer=None):
    headers = {"User-Agent": BROWSER_UA if referer else UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(urllib.parse.quote(url, safe=":/?=&%#"), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit)


def http_get(url, timeout=25):
    return http_bytes(url, timeout).decode("utf-8", "replace")


class Cache:
    """Keeps the last good value. Serves it at once and refreshes in the background when it is older than
    `ttl`. Only one refresh runs at a time. A failed refresh keeps the old value and retries after `retry`."""

    def __init__(self, name, fn, ttl, retry=300):
        self.name, self.fn, self.ttl, self.retry = name, fn, ttl, retry
        self.value, self.at, self.err, self.tried = None, 0.0, None, 0.0
        self.lock = threading.Lock()
        self.running = False

    def _run(self):
        try:
            v = self.fn()
            self.value, self.at, self.err = v, time.time(), None
            print(f"[data] {self.name} refreshed", flush=True)
        except Exception as e:  # keep the last good value
            self.err = str(e)[:300]
            print(f"[data] {self.name} failed: {self.err}", flush=True)
        finally:
            self.tried = time.time()
            self.running = False

    def _start(self, wait):
        with self.lock:
            if self.running:
                start = False
            else:
                self.running, start = True, True
        if start:
            t = threading.Thread(target=self._run, daemon=True)
            t.start()
            if wait:
                t.join(timeout=90)
        elif wait:
            for _ in range(180):
                if not self.running:
                    break
                time.sleep(0.5)

    def get(self, force=False):
        age = time.time() - self.at
        stale = self.value is None or age > self.ttl or force
        recently_failed = time.time() - self.tried < self.retry
        if stale and not (recently_failed and not force and self.value is not None):
            self._start(wait=self.value is None)
        return {
            "data": self.value,
            "updated": datetime.fromtimestamp(self.at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if self.at else None,
            "age_s": int(time.time() - self.at) if self.at else None,
            "refreshing": self.running,
            "error": self.err if self.value is None else None,
            "next_s": max(0, int(self.ttl - (time.time() - self.at))) if self.at else 0,
        }


# ---------------- Spices Board of India ----------------
DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")


def _cell(s):
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _num(s):
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_spices_board(page):
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        cells = [_cell(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        if len(cells) < 7 or not DATE_RE.match(cells[0]):
            continue
        # Date, Spice, Market centre, State, Grade, Source, Min, Max, Avg (older layouts may have fewer columns)
        prices = [_num(c) for c in cells[-3:]]
        avg = prices[2] if prices[2] is not None else (prices[1] if prices[1] is not None else prices[0])
        if avg is None:
            continue
        d = datetime.strptime(cells[0], "%d-%b-%Y").strftime("%Y-%m-%d")
        rows.append({
            "date": d, "spice": cells[1], "market": cells[2].title(), "state": cells[3].title(),
            "grade": "" if cells[4] in ("-", "") else cells[4], "via": cells[5] if len(cells) >= 9 else "",
            "min": prices[0], "max": prices[1], "price": avg, "unit": "₹/kg",
            "source": "Spices Board of India", "url": SPICES_BOARD, "official": True,
        })
    return rows


def fetch_spices_board():
    rows = parse_spices_board(http_get(SPICES_BOARD))
    if not rows:
        raise RuntimeError("no price rows found on the Spices Board page")
    return rows


# ---------------- Gemini with Google Search ----------------
def grounded_payload(prompt):
    """A Gemini request with Google Search turned on, so answers come from current web pages."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 6000},
    }


def parse_json_block(text):
    m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, flags=re.S)
    raw = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


INDIA_EXTRA = ["Small cardamom", "Large cardamom", "Turmeric", "Red chilli", "Cumin (jeera)", "Coriander seed",
               "Dry ginger", "Fennel", "Fenugreek", "Mustard seed", "Black pepper"]
WORLD = ["Black pepper (Vietnam export)", "Black pepper (Brazil export)", "Cardamom (Guatemala export)",
         "Cinnamon / cassia (Indonesia or Vietnam)", "Cloves (Madagascar or Indonesia)", "Nutmeg (Indonesia)",
         "Vanilla (Madagascar)", "Ginger (China export)", "Turmeric (India export)", "Cumin (India export)",
         "Chilli (India export)"]


def price_prompt():
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    return f"""Today is {today}. Use Google Search to find the most recent real market prices for these spices.

INDIA (domestic wholesale or auction prices in Indian markets, in rupees per kg): {", ".join(INDIA_EXTRA)}.
WORLD (international or export prices, in US dollars per kg; convert from per tonne by dividing by 1000): {", ".join(WORLD)}.

Rules:
- Only include a price you actually found in a search result dated within the last 45 days. Never estimate,
  average across years, or invent a number. If you can't find a recent price, leave that spice out.
- For a range, give the midpoint as "price" and the ends as "min" and "max".
- "date" is the date the price applies to (YYYY-MM-DD). "source" is the publisher's name (for example
  "Spices Board of India", "Commodity Online", "The Hindu BusinessLine", "Nedspice", "Tridge").
- "trend" is "up", "down" or "steady" only if the source says how the price moved; otherwise "".

Reply with ONLY this JSON, nothing else:
{{"india":[{{"spice":"Small cardamom","market":"Idukki auctions","grade":"average","price":2400,"min":null,"max":null,"unit":"₹/kg","date":"2026-11-05","source":"Spices Board of India","trend":""}}],
 "world":[{{"spice":"Black pepper","market":"Vietnam, FOB export","grade":"500 g/l","price":6.5,"min":6.4,"max":6.6,"unit":"US$/kg","date":"2026-11-05","source":"Nedspice","trend":"up"}}]}}"""


def to_num(v):
    """Numbers as printed in reports: 2,398.40 · Rs.2398 · 180-220 (midpoint) · '-'."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip()
    m = re.findall(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    if len(m) >= 2 and re.search(r"\d\s*[-–to]+\s*\d", s):
        return (float(m[0]) + float(m[1])) / 2
    return float(m[0])


def clean_rows(rows, unit):
    out = []
    for r in rows or []:
        p = to_num(r.get("price"))
        if p is None:
            mn, mx = to_num(r.get("min")), to_num(r.get("max"))
            p = (mn + mx) / 2 if mn and mx else (mx or mn)
        if p is None:
            continue
        if p <= 0:
            continue
        d = str(r.get("date") or "")[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            d = ""
        out.append({
            "spice": str(r.get("spice", "")).strip()[:60], "market": str(r.get("market", "")).strip()[:80],
            "grade": str(r.get("grade", "") or "").strip()[:60], "price": round(p, 2),
            "min": to_num(r.get("min")), "max": to_num(r.get("max")),
            "unit": unit, "date": d, "source": str(r.get("source", "")).strip()[:80],
            "trend": r.get("trend") if r.get("trend") in ("up", "down", "steady") else "", "official": False, "ai": True,
        })
    return [r for r in out if r["spice"]]


# ---------------- IMF commodity prices via FRED ----------------
COMMODITIES = [
    ("POILBREUSDM", "Crude oil (Brent)", "US$/barrel", "Energy"),
    ("PWHEAMTUSDM", "Wheat", "US$/tonne", "Grains"),
    ("PRICENPQUSDM", "Rice (Thai)", "US$/tonne", "Grains"),
    ("PMAIZMTUSDM", "Maize (corn)", "US$/tonne", "Grains"),
    ("PSOYBUSDM", "Soybeans", "US$/tonne", "Oilseeds"),
    ("PPOILUSDM", "Palm oil", "US$/tonne", "Oilseeds"),
    ("PSUGAISAUSDM", "Sugar", "US cents/lb", "Softs"),
    ("PCOFFOTMUSDM", "Coffee (Arabica)", "US cents/lb", "Softs"),
    ("PCOFFROBUSDM", "Coffee (Robusta)", "US cents/lb", "Softs"),
    ("PCOCOUSDM", "Cocoa", "US$/tonne", "Softs"),
    ("PCOTTINDUSDM", "Cotton", "US cents/lb", "Fibres"),
    ("PCOPPUSDM", "Copper", "US$/tonne", "Metals"),
]
FX = ("DEXINUS", "Rupees per US dollar", "₹ per US$", "Currency")


def fred_series(sid, months=60):
    text = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
    pts = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not re.match(r"^\d{4}-\d{2}-\d{2}$", row[0]):
            continue
        try:
            pts.append([row[0], round(float(row[1]), 4)])
        except ValueError:
            continue
    if not pts:
        raise RuntimeError(f"{sid}: no data")
    return pts[-months:] if months else pts


def fetch_commodities():
    out, errors = [], []
    lock = threading.Lock()

    def one(sid, name, unit, group, months):
        try:
            pts = fred_series(sid, months)
            with lock:
                out.append({"id": sid, "name": name, "unit": unit, "group": group, "points": pts,
                            "source": "IMF Primary Commodity Prices, via FRED",
                            "url": f"https://fred.stlouisfed.org/series/{sid}"})
        except Exception as e:
            with lock:
                errors.append(f"{sid}: {str(e)[:80]}")

    threads = [threading.Thread(target=one, args=(*c, 60)) for c in COMMODITIES]
    threads.append(threading.Thread(target=one, args=(*FX, 400)))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    if not out:
        raise RuntimeError("; ".join(errors) or "no commodity data")
    order = {c[0]: i for i, c in enumerate(COMMODITIES + [FX])}
    out.sort(key=lambda x: order.get(x["id"], 99))
    fx = next((x for x in out if x["id"] == FX[0]), None)
    return {"items": [x for x in out if x["id"] != FX[0]], "fx": fx, "errors": errors}


def ehedg_prompt():
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    return f"""Today is {today}. Use Google Search to find the latest news from EHEDG (the European Hygienic
Engineering & Design Group, ehedg.org): new or revised guidelines, guidelines under revision, certification
news, and upcoming events or congresses. Prefer items from the last 12 months and anything about dry food or
spice processing.

Reply with ONLY this JSON (at most 8 items, newest first, no invented items):
{{"items":[{{"title":"short headline","date":"YYYY-MM-DD or YYYY-MM or empty","kind":"guideline|certification|event|news","summary":"one or two plain sentences","source":"publisher name"}}]}}"""


# ---------------- Spices Board weekly "Spice Market" report (PDF), read by Gemini ----------------
WEEKLY_LIST = "https://indianspices.com/magazines/english/sm-weekly.html"


def latest_weekly():
    page = http_get(WEEKLY_LIST)
    links = [html.unescape(x) for x in re.findall(r'href=["\']([^"\']+?\.pdf)["\']', page, flags=re.I)]
    dated = [x for x in links if re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", urllib.parse.unquote(x))]

    def when(x):
        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", urllib.parse.unquote(x))
        return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (0, 0, 0)
    if dated:
        links = sorted(dated, key=when, reverse=True)
    if not links:
        raise RuntimeError("no weekly report link found")
    url = urllib.parse.urljoin(WEEKLY_LIST, links[0])
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", urllib.parse.unquote(url))
    week = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""
    return url, week


def weekly_payload(pdf_bytes, week):
    prompt = f"""This PDF is the Spices Board of India's weekly "Spice Market" report{(' for the week ending ' + week) if week else ''}.
Read every price in it and return them as data. Copy numbers exactly as printed; do not calculate or guess.
- Cardamom auctions: give one row for small cardamom with the weekly total average price (market "Kerala & Tamil Nadu auctions").
- Daily black pepper prices: give one row per market and grade using the latest day that has a price.
- Wholesale prices: one row per spice, market centre and grade for THIS week (not last year). If a min and max
  are printed, put them in "min" and "max" and their midpoint in "price".
"date" is the date the price is for (YYYY-MM-DD; use the week-ending date if only the week is given).

Reply with ONLY this JSON:
{{"week":"YYYY-MM-DD","rows":[{{"spice":"Chilli","market":"Guntur","grade":"Teja","min":180,"max":220,"price":200,"date":"YYYY-MM-DD"}}]}}"""
    return {
        "contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()}},
            {"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 12000},
    }


# ---------------- EHEDG news, read from ehedg.org ----------------
EHEDG_PAGES = ["https://www.ehedg.org/news-events/news/ehedg-news",
               "https://www.ehedg.org/news-events/events-activities/ehedg-global-events"]


def page_text(url, limit=14000):
    raw = http_get(url)
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<a [^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", lambda m: f" {m.group(2)} [link: {urllib.parse.urljoin(url, m.group(1))}] ", raw)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    return text[:limit]


def ehedg_payload(texts):
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    joined = "\n\n".join(f"PAGE {u}:\n{tx}" for u, tx in texts)
    prompt = f"""Today is {today}. Below is the text of EHEDG's official News and Events pages (ehedg.org).
List the most recent news items and upcoming events found on these pages, newest first, at most 8.
Only use what is on the pages. Write each summary in your own plain words (one or two sentences).

Reply with ONLY this JSON:
{{"items":[{{"title":"short headline","date":"YYYY-MM-DD or YYYY-MM or empty","kind":"guideline|certification|event|news","summary":"...","source":"EHEDG","url":"link from the page if shown"}}]}}

{joined}"""
    return {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4000}}
