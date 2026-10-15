#!/usr/bin/env python3
"""Refresh the 20%+ EPS growth screen from BSE, NSE and Screener.

1. Pull result filings from BSE (Result category) and NSE (financial results)
   for the last few days.
2. For each filer, read Screener's quarterly table (consolidated, else
   standalone): EPS latest / prior quarter / year-ago quarter, market cap.
3. Keep companies with market cap >= MIN_MCAP and YoY and QoQ EPS growth
   both >= MIN_GROWTH. Close vs 21 EMA comes from Screener's price chart.
4. Merge into the screen keyed by symbol: a new filing replaces the old row,
   and a company whose new result fails the screen is dropped.

Stdlib only. Used by app.py; can also run alone:
  python refresh_eps_screen.py --in data/screen.json --out data/screen.json
"""
import argparse, csv, datetime as dt, gzip, html, http.cookiejar, json, re, ssl, sys, time, urllib.error, urllib.parse, urllib.request

MIN_MCAP = 2000.0      # Rs crore
MIN_GROWTH = 0.20
PAUSE = 1.5            # seconds between Screener requests, to stay polite
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
COLS = ["company", "statement", "quarter", "yoy", "qoq", "eps_latest", "eps_prev",
        "eps_yearago", "symbol", "close", "ema21", "above_ema21", "mcap_cr",
        "result_date", "exchange_src", "updated_at"]


def _ssl_context():
    # python.org builds on Mac ship without system certificates; certifi fixes that if installed
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                     urllib.request.HTTPSHandler(context=_ssl_context()))


def get(url, referer=None, as_json=False, tries=3):
    hdr = {"User-Agent": UA, "Accept": "application/json, text/html, */*",
           "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip"}
    if referer:
        hdr["Referer"] = referer
        hdr["Origin"] = "/".join(referer.split("/")[:3])
    last = None
    for i in range(tries):
        try:
            with opener.open(urllib.request.Request(url, headers=hdr), timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                text = body.decode("utf-8", "replace")
                return json.loads(text) if as_json else text
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                return None
            if e.code == 429:          # rate limited: back off hard
                time.sleep(30 * (i + 1))
                continue
        except Exception as e:         # network hiccup, bad JSON
            last = e
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"{url.split('?')[0]}: {last}")


def iso_when(s):
    """Normalise an exchange timestamp to 'YYYY-MM-DDTHH:MM:SS' (IST)."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y",
                "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s[:len(dt.datetime(2026, 12, 31, 23, 59, 59).strftime(fmt))], fmt).isoformat()
        except ValueError:
            continue
    return dt.datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S")


# ---------- 1. filings ----------

def bse_filings(start, end):
    out, page = {}, 1
    while page <= 40:
        url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?"
               + urllib.parse.urlencode({"pageno": page, "strCat": "Result",
                                         "strPrevDate": start.strftime("%Y%m%d"),
                                         "strScrip": "", "strSearch": "P",
                                         "strToDate": end.strftime("%Y%m%d"),
                                         "strType": "C", "subcategory": "-1"}))
        d = get(url, referer="https://www.bseindia.com/", as_json=True) or {}
        rows = d.get("Table") or []
        if not rows:
            break
        for r in rows:
            code = str(r.get("SCRIP_CD") or "").strip()
            if not code:
                continue
            when = iso_when(r.get("DT_TM") or r.get("NEWS_DT"))
            prev = out.get(code)
            if not prev or when > prev["when"]:
                out[code] = {"bse": code, "name": r.get("SLONGNAME") or "", "when": when}
        page += 1
    return out


def nse_filings(start, end):
    get("https://www.nseindia.com/", tries=2)  # sets the cookies NSE's API needs
    url = ("https://www.nseindia.com/api/corporates-financial-results?"
           + urllib.parse.urlencode({"index": "equities", "period": "Quarterly",
                                     "from_date": start.strftime("%d-%m-%Y"),
                                     "to_date": end.strftime("%d-%m-%Y")}))
    d = get(url, referer="https://www.nseindia.com/companies-listing/corporate-filings-financial-results",
            as_json=True) or []
    out = {}
    for r in d if isinstance(d, list) else d.get("data", []):
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        when = iso_when(r.get("broadCastDate") or r.get("filingDate") or r.get("exchdisstime"))
        prev = out.get(sym)
        if not prev or when > prev["when"]:
            out[sym] = {"nse": sym, "name": r.get("companyName") or "", "when": when}
    return out


RESULT_RE = re.compile(r"financial results?|results? for the (quarter|period)", re.I)
NOT_RESULT_RE = re.compile(r"to be held|will be held|intimation|prior notice|to consider|trading window|"
                           r"newspaper|advertisement|schedule of|investor (meet|call)|earnings call|"
                           r"conference call|transcript|audio|recording", re.I)


def _items(xml):
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        g = lambda tag: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", (re.search(
            rf"<{tag}>(.*?)</{tag}>", it, re.S) or [None, ""])[1])).strip()
        yield {k: g(k) for k in ("title", "link", "description", "pubDate", "scripcode")}


def bse_filings_rss():
    """BSE's live corporate-announcements feed (rolling, roughly today's filings)."""
    xml = get("https://www.bseindia.com/data/xml/announcements.xml") or ""
    out = {}
    for it in _items(xml.lstrip("﻿")):
        code, desc = it["scripcode"], it["description"]
        if not code or not RESULT_RE.search(desc) or NOT_RESULT_RE.search(desc):
            continue
        when = iso_when(it["pubDate"])
        name = re.sub(r"\s*\(\d+\)\s*$", "", it["title"])
        if code not in out or when > out[code]["when"]:
            out[code] = {"bse": code, "name": name, "when": when}
    return out


def nse_filings_rss():
    """NSE's live announcements feed; the symbol comes from the attachment's file name."""
    xml = get("https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml") or ""
    out = {}
    for it in _items(xml):
        desc = it["description"]
        if not RESULT_RE.search(desc) or NOT_RESULT_RE.search(desc.split("|SUBJECT:")[0]):
            continue
        m = re.search(r"/corporate/([A-Z0-9&\-]+)_\d{8,}", it["link"])
        if not m:
            continue
        sym, when = m.group(1), iso_when(it["pubDate"])
        if sym not in out or when > out[sym]["when"]:
            out[sym] = {"nse": sym, "name": it["title"], "when": when}
    return out


# ---------- 2. Screener ----------

def num(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", str(s or ""))).replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_screener(page):
    """Return dict with name, mcap, quarters [(label, eps)], company/warehouse ids."""
    if not page:
        return None
    name = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    mcap = re.search(r'Market Cap.*?<span class="number">([^<]+)</span>', page, re.S)
    cid = re.search(r'data-company-id="(\d+)"', page)
    wid = re.search(r'data-warehouse-id="(\d+)"', page)
    sec = re.search(r'<section id="quarters".*?</section>', page, re.S)
    if not sec:
        return None
    sec = sec.group(0)
    heads = [html.unescape(re.sub(r"<[^>]+>", "", h)).strip()
             for h in re.findall(r"<th[^>]*>(.*?)</th>", sec, re.S)]
    eps = None
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", sec, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if cells and html.unescape(re.sub(r"<[^>]+>|\s+", " ", cells[0])).strip().startswith("EPS"):
            eps = [num(c) for c in cells[1:]]
            break
    if not eps:
        return None
    labels = [h for h in heads if re.match(r"[A-Z][a-z]{2} \d{4}$", h)]
    quarters = list(zip(labels[-len(eps):], eps[-len(labels):]))
    return {"name": html.unescape(re.sub(r"<[^>]+>", "", name.group(1))).strip() if name else "",
            "mcap": num(mcap.group(1)) if mcap else None, "quarters": quarters,
            "ids": [m.group(1) for m in (cid, wid) if m]}


def screener_company(ident):
    for stmt, path in (("Consolidated", "consolidated/"), ("Standalone", "")):
        p = parse_screener(get(f"https://www.screener.in/company/{ident}/{path}"))
        time.sleep(PAUSE)
        # Screener shows an empty consolidated table for standalone-only filers
        if p and len([e for _, e in p["quarters"] if e is not None]) >= 5:
            p["statement"] = stmt
            return p
    return None


def ema21(ids, consolidated):
    for cid in ids:
        url = (f"https://www.screener.in/api/company/{cid}/chart/?q=Price-DMA50-DMA200-Volume"
               f"&days=120{'&consolidated=true' if consolidated else ''}")
        try:
            d = get(url, referer="https://www.screener.in/", as_json=True, tries=2) or {}
        except RuntimeError:
            d = {}
        time.sleep(PAUSE)
        prices = []
        for ds in d.get("datasets", []):
            if ds.get("metric") == "Price":
                prices = [float(v[1]) for v in ds.get("values", []) if v[1] not in (None, "")]
        if len(prices) >= 21:
            k, e = 2 / 22, sum(prices[:21]) / 21
            for p in prices[21:]:
                e = p * k + e * (1 - k)
            return prices[-1], round(e, 2)
    return None, None


def growth(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


def qlabel(lbl):  # "Sep 2026" -> "Sep 26 (Q2)" (Indian FY quarters)
    m, y = lbl.split()
    q = {"Jun": 1, "Sep": 2, "Dec": 3, "Mar": 4}.get(m)
    return f"{m} {y[2:]}" + (f" (Q{q})" if q else "")


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_name(n):
    n = n.lower().replace("limited", "ltd").replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", n)[:14]


def evaluate(ident, symbol, when, src):
    # ident may be a list of Screener ids to try in order (NSE symbol, cleaned symbol, BSE code)
    p = None
    for i in (ident if isinstance(ident, list) else [ident]):
        p = screener_company(i)
        if p:
            break
    if not p or len(p["quarters"]) < 5:
        return None, "no Screener quarterly EPS"
    q = p["quarters"]
    (lbl, e0), (_, e1), (_, e4) = q[-1], q[-2], q[-5]
    # Screener can lag a fresh filing: the latest quarter must have ended within ~80 days of it
    qend = dt.datetime.strptime(lbl, "%b %Y").date().replace(day=28)
    if (dt.date.fromisoformat(when[:10]) - qend).days > 80:
        return None, "pending: Screener not updated yet"
    yoy, qoq = growth(e0, e4), growth(e0, e1)
    passes = (p["mcap"] or 0) >= MIN_MCAP and yoy is not None and qoq is not None \
        and yoy >= MIN_GROWTH and qoq >= MIN_GROWTH
    if not passes:
        return None, f"fails screen (mcap {p['mcap']}, yoy {yoy}, qoq {qoq})"
    close, ema = ema21(p["ids"], p["statement"] == "Consolidated")
    return {"company": p["name"], "statement": p["statement"], "quarter": qlabel(lbl),
            "yoy": round(yoy, 4), "qoq": round(qoq, 4), "eps_latest": e0, "eps_prev": e1,
            "eps_yearago": e4, "symbol": symbol, "close": close if close is not None else "",
            "ema21": ema if ema is not None else "",
            "above_ema21": "" if ema is None else ("1" if close > ema else "0"),
            "mcap_cr": p["mcap"], "result_date": when[:10], "exchange_src": src,
            "updated_at": now_utc()}, "pass"


# ---------- 3. merge ----------

def key_of(r):
    # Rows with no ticker are kept under their name so they don't collapse together
    return str(r.get("symbol") or "") or f"NAME:{r['company']}"


def to_rows(src):
    return {key_of(r): {c: r.get(c, "") for c in COLS} for r in src}


def to_list(rows):
    seen, out = set(), []
    for r in sorted(rows.values(), key=lambda r: -float(r["yoy"] or 0)):
        if r["company"] in seen:   # company name is the table's row key: keep it unique
            r["company"] = f"{r['company']} ({r['symbol']})"
        seen.add(r["company"]); out.append(r)
    return out


def refresh(rows, seen=None, days=2, progress=lambda msg: None, pending=None):
    """Fold new filings into rows (dict keyed by symbol). `seen` is a set of
    'symbol|filing time' already evaluated, so each filing is read once."""
    seen = set() if seen is None else seen
    pending = {} if pending is None else pending   # symbol -> [ident, when, src]
    end = dt.datetime.now(IST).date()
    start = end - dt.timedelta(days=days)
    log = {"started": now_utc(), "bse": 0, "nse": 0, "checked": 0, "added": [],
           "updated": [], "dropped": [], "errors": []}

    filers, nse, bse = {}, {}, {}
    progress("Reading NSE filings")
    for fn in (nse_filings_rss, lambda: nse_filings(start, end)):   # live feed first, API as fallback
        try:
            nse = fn(); break
        except Exception as e:
            log["errors"].append(f"NSE: {e}")
    if nse:
        log["errors"] = [e for e in log["errors"] if not e.startswith("NSE")]
    log["nse"] = len(nse)
    by_name = {}
    for s, r in nse.items():
        ids = [s] + ([s.rstrip("0123456789")] if s.rstrip("0123456789") not in ("", s) else [])
        filers[f"NSE:{ids[-1]}"] = (ids, r["when"], "NSE")   # cleaned symbol matches existing rows
        by_name[norm_name(r["name"])] = f"NSE:{ids[-1]}"
    progress("Reading BSE filings")
    for fn in (bse_filings_rss, lambda: bse_filings(start, end)):
        try:
            bse = fn(); break
        except Exception as e:
            log["errors"].append(f"BSE: {e}")
    if bse:
        log["errors"] = [e for e in log["errors"] if not e.startswith("BSE")]
    log["bse"] = len(bse)
    try:
        for c, r in bse.items():
            k = by_name.get(norm_name(r["name"]))
            if k:   # same company filed on NSE: keep its BSE code as a fallback Screener id
                filers[k][0].append(c)
                continue
            filers.setdefault(f"BSE:{c}", ([c], r["when"], "BSE"))
    except Exception as e:
        log["errors"].append(f"BSE: {e}")

    # Filings whose quarter Screener hadn't loaded yet are retried for 3 days
    cutoff = (dt.datetime.now(IST) - dt.timedelta(days=3)).isoformat()
    for s, v in list(pending.items()):
        if v[1] < cutoff:
            del pending[s]
        else:
            filers.setdefault(s, tuple(v))
    todo = [(s, v) for s, v in filers.items() if f"{s}|{v[1]}" not in seen]
    for i, (sym, (ident, when, src)) in enumerate(todo, 1):
        progress(f"Checking {i}/{len(todo)}: {sym}")
        old = rows.get(sym)
        log["checked"] += 1
        try:
            new, why = evaluate(ident, sym, when, src)
        except Exception as e:
            log["errors"].append(f"{sym}: {e}")
            if len(log["errors"]) >= 5 and log["checked"] == len(log["errors"]):
                log["errors"].append("Stopped early: Screener not reachable; will retry next check")
                break
            continue
        if why.startswith("pending"):
            pending[sym] = [ident, when, src]
            log.setdefault("pending", []).append(sym)
            continue
        pending.pop(sym, None)
        seen.add(f"{sym}|{when}")
        if new:
            log["updated" if old else "added"].append(new["company"])
            rows[sym] = new
        elif old and why.startswith("fails"):
            log["dropped"].append(old["company"]); del rows[sym]
    log["finished"] = now_utc()
    log["ok"] = not (log["bse"] == 0 and log["nse"] == 0 and log["errors"])
    return rows, log


def refresh_ema_all(rows, progress=lambda msg: None):
    """Re-price every row's close vs 21 EMA (run once a day after market close)."""
    errors, items = [], [(k, r) for k, r in rows.items() if not k.startswith("NAME:")]
    for i, (sym, r) in enumerate(items, 1):
        progress(f"Updating 21 EMA {i}/{len(items)}")
        try:
            p = parse_screener(get(f"https://www.screener.in/company/{sym.split(':', 1)[1]}/"))
            time.sleep(PAUSE)
            if p:
                close, ema = ema21(p["ids"], r["statement"] == "Consolidated")
                if ema is not None:
                    r.update(close=close, ema21=ema, above_ema21="1" if close > ema else "0")
        except Exception as e:
            errors.append(f"EMA {sym}: {e}")
            if len(errors) >= 5 and len(errors) == i:   # Screener unreachable: stop, retry tomorrow
                errors.append("EMA update stopped early: Screener not reachable")
                break
    return errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--refresh-ema", action="store_true")
    a = ap.parse_args()
    with open(a.inp, newline="", encoding="utf-8") as f:
        src = list(csv.DictReader(f)) if a.inp.endswith(".csv") else json.load(f)
    rows, log = refresh(to_rows(src), days=a.days, progress=lambda m: print(m, file=sys.stderr))
    if a.refresh_ema:
        log["errors"] += refresh_ema_all(rows)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(to_list(rows), f, ensure_ascii=False)
    print(json.dumps(log, ensure_ascii=False))
    sys.exit(0 if log["ok"] else 2)


if __name__ == "__main__":
    main()
