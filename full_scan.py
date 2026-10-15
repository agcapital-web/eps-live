#!/usr/bin/env python3
"""One-time backfill: screen every NSE-listed company on Screener.

The live feeds only hold the current day's filings, so results filed before
the site went live (or on a day a run was missed) are caught here. Any
company whose latest reported quarter is QUARTER and passes the screen is
added; a company already listed whose new QUARTER result fails is dropped.

  QUARTER="Sep 2026" python full_scan.py
"""
import csv, datetime as dt, io, json, os, sys, time
import refresh_eps_screen as eng

QUARTER = os.environ.get("QUARTER", "Sep 2026")
HERE = os.path.dirname(os.path.abspath(__file__))
SCREEN = os.path.join(HERE, "docs", "screen.json")


def universe():
    text = eng.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    key = next(k for k in rows[0] if k.strip().upper() == "SYMBOL")
    return sorted({r[key].strip() for r in rows if r[key].strip()})


def scan_one(sym):
    """Return (row or None, reason)."""
    page = eng.get(f"https://www.screener.in/company/{sym}/consolidated/")
    time.sleep(eng.PAUSE)
    p = eng.parse_screener(page)
    mcap = p["mcap"] if p else None
    if mcap is not None and mcap < eng.MIN_MCAP:
        return None, "small"
    stmt = "Consolidated"
    if not p or len([e for _, e in p["quarters"] if e is not None]) < 5:
        p = eng.parse_screener(eng.get(f"https://www.screener.in/company/{sym}/"))
        time.sleep(eng.PAUSE)
        stmt = "Standalone"
        if not p or len(p["quarters"]) < 5:
            return None, "no data"
        if (p["mcap"] or 0) < eng.MIN_MCAP:
            return None, "small"
    q = p["quarters"]
    if q[-1][0] != QUARTER:
        return None, "not reported"
    (lbl, e0), (_, e1), (_, e4) = q[-1], q[-2], q[-5]
    yoy, qoq = eng.growth(e0, e4), eng.growth(e0, e1)
    if yoy is None or qoq is None or yoy < eng.MIN_GROWTH or qoq < eng.MIN_GROWTH:
        return None, "fails"
    close, ema = eng.ema21(p["ids"], stmt == "Consolidated")
    today = dt.datetime.now(eng.IST).date().isoformat()
    return {"company": p["name"], "statement": stmt, "quarter": eng.qlabel(lbl),
            "yoy": round(yoy, 4), "qoq": round(qoq, 4), "eps_latest": e0, "eps_prev": e1,
            "eps_yearago": e4, "symbol": f"NSE:{sym}", "close": close if close is not None else "",
            "ema21": ema if ema is not None else "",
            "above_ema21": "" if ema is None else ("1" if close > ema else "0"),
            "mcap_cr": p["mcap"], "result_date": today, "exchange_src": "Full scan",
            "updated_at": eng.now_utc()}, "pass"


def main():
    syms = universe()
    print(f"{len(syms)} NSE symbols; looking for {QUARTER} results", flush=True)
    found, fails, counts, errors = {}, set(), {}, []
    streak = 0
    for i, s in enumerate(syms, 1):
        try:
            row, why = scan_one(s)
            streak = 0
        except Exception as e:
            row, why = None, "error"
            errors.append(f"{s}: {e}")
            streak += 1
            if streak >= 15:
                print("Screener stopped answering; ending scan early", flush=True)
                break
        counts[why] = counts.get(why, 0) + 1
        if row:
            found[f"NSE:{s}"] = row
        elif why == "fails":
            fails.add(f"NSE:{s}")
        if i % 100 == 0:
            print(f"{i}/{len(syms)} · {counts}", flush=True)

    doc = json.load(open(SCREEN, encoding="utf-8"))
    rows = eng.to_rows(doc["rows"])
    added, updated, dropped = [], [], []
    for k, r in found.items():
        old = rows.get(k)
        if old and old.get("quarter") == r["quarter"]:
            r["result_date"] = old.get("result_date") or r["result_date"]   # keep a real filing date
        (updated if old else added).append(r["company"])
        rows[k] = r
    for k in fails:
        if k in rows:
            dropped.append(rows.pop(k)["company"])
    log = {"started": eng.now_utc(), "finished": eng.now_utc(), "bse": 0, "nse": len(syms),
           "checked": sum(counts.values()), "added": added, "updated": updated, "dropped": dropped,
           "errors": errors[:20], "ok": True, "full_scan": counts}
    st = doc.get("status") or {}
    st["history"] = ([log] + st.get("history", []))[:50]
    st["last"] = log
    doc.update(rows=eng.to_list(rows), status=st)
    json.dump(doc, open(SCREEN, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in log.items()}))
    print("Added:", ", ".join(added))
    print("Dropped:", ", ".join(dropped))


if __name__ == "__main__":
    main()
