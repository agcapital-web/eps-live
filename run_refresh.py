#!/usr/bin/env python3
"""One refresh pass for GitHub Actions: folds new BSE/NSE result filings into
docs/screen.json (rows + status, the shape the page reads)."""
import datetime as dt, json, os
import refresh_eps_screen as eng

HERE = os.path.dirname(os.path.abspath(__file__))
SCREEN, STATE = os.path.join(HERE, "docs", "screen.json"), os.path.join(HERE, "state.json")
EVERY_MIN = 15


def load(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return d


doc = load(SCREEN, {"rows": []})
state = load(STATE, {"seen": [], "ema_day": "", "history": [], "pending": {}})
seen = set(state["seen"])
state.setdefault("pending", {})
rows, log = eng.refresh(eng.to_rows(doc["rows"]), seen, days=2, progress=print, pending=state["pending"])
# Manual check: SYMBOLS="BHEL,LT" screens those companies now, filed or not (backfills missed results)
manual = [s.strip().upper() for s in os.environ.get("SYMBOLS", "").split(",") if s.strip()]
if manual:
    when = dt.datetime.now(eng.IST).strftime("%Y-%m-%dT%H:%M:%S")
    log["manual"] = []
    for s in manual:
        key = s if ":" in s else (f"BSE:{s}" if s.isdigit() else f"NSE:{s}")
        ident = key.split(":", 1)[1]
        try:
            new, why = eng.evaluate(ident, key, when, "Manual")
        except Exception as e:
            new, why = None, f"error: {e}"
        old = rows.get(key)
        if new:
            (log["updated"] if old else log["added"]).append(new["company"])
            rows[key] = new
            why = f"passes: YoY {new['yoy']:.0%}, QoQ {new['qoq']:.0%}, {new['quarter']}"
        elif old and why.startswith("fails"):
            log["dropped"].append(old["company"]); del rows[key]
        log["manual"].append(f"{s}: {why}")
        print(f"{s}: {why}")
now = dt.datetime.now(eng.IST)
if log["ok"] and now.hour >= 16 and state.get("ema_day") != now.date().isoformat():
    log["errors"] += eng.refresh_ema_all(rows, progress=print)
    state["ema_day"] = now.date().isoformat()
state["seen"] = sorted(seen)[-5000:]
state["history"] = ([log] + state.get("history", []))[:50]
nxt = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=EVERY_MIN)).strftime("%Y-%m-%dT%H:%M:%SZ")
with open(SCREEN, "w", encoding="utf-8") as f:
    json.dump({"rows": eng.to_list(rows), "status": {"running": False, "step": "", "last": log,
               "next_at": nxt, "history": state["history"]}}, f, ensure_ascii=False)
with open(STATE, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False)
print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in log.items()}))
