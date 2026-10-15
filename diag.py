"""One-off: which data endpoints answer GitHub's servers? Writes diag.txt."""
import datetime as dt, refresh_eps_screen as eng
d = dt.date.today(); y = d - dt.timedelta(days=2)
urls = [
 ("BSE api", f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=Result&strPrevDate={y:%Y%m%d}&strScrip=&strSearch=P&strToDate={d:%Y%m%d}&strType=C&subcategory=-1", "https://www.bseindia.com/"),
 ("BSE home", "https://www.bseindia.com/", None),
 ("BSE rss", "https://www.bseindia.com/data/xml/notices.xml", None),
 ("NSE home", "https://www.nseindia.com/", None),
 ("NSE rss results", "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml", None),
 ("NSE rss announcements", "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", None),
 ("Screener latest results", "https://www.screener.in/results/latest/", None),
 ("Screener company", "https://www.screener.in/company/RELIANCE/consolidated/", None),
 ("Screener chart", "https://www.screener.in/api/company/2726/chart/?q=Price-DMA50-DMA200-Volume&days=30", "https://www.screener.in/"),
]
out = []
for name, u, ref in urls:
    try:
        t = eng.get(u, referer=ref, tries=1)
        out.append(f"OK   {name}: {len(t or '')} chars | {(t or '')[:300]!r}")
    except Exception as e:
        out.append(f"FAIL {name}: {e}")
open("diag.txt", "w").write("\n".join(out) + "\n")
print("\n".join(out))
