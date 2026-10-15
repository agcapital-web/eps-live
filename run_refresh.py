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
state = load(STATE, {"seen": [], "ema_day": "", "history": []})
seen = set(state["seen"])
rows, log = eng.refresh(eng.to_rows(doc["rows"]), seen, days=2, progress=print)
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
