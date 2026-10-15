"""One-off: save raw samples of reachable sources into diag/ for parser work."""
import os, datetime as dt, refresh_eps_screen as eng
os.makedirs("diag", exist_ok=True)
def save(name, u, ref=None):
    try:
        t = eng.get(u, referer=ref, tries=1) or ""
        open(f"diag/{name}", "w").write(t if isinstance(t, str) else str(t))
    except Exception as e:
        open(f"diag/{name}", "w").write(f"FAIL {e}")
save("nse_results.xml", "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml")
save("nse_ann.xml", "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml")
save("screener_rel.html", "https://www.screener.in/company/RELIANCE/consolidated/")
save("screener_bse.html", "https://www.screener.in/company/500325/")
# BSE API after picking up cookies from the home page
eng.get("https://www.bseindia.com/", tries=1)
d = dt.date.today(); y = d - dt.timedelta(days=2)
save("bse_api.json", f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=Result&strPrevDate={y:%Y%m%d}&strScrip=&strSearch=P&strToDate={d:%Y%m%d}&strType=C&subcategory=-1", "https://www.bseindia.com/")
save("bse_rss_results.xml", "https://www.bseindia.com/data/xml/announcements.xml")
