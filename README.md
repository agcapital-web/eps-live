# EPS Growth Live

20%+ EPS growth screen for Indian listed companies (market cap ≥ ₹2,000 Cr, YoY and QoQ EPS growth both ≥ 20%).

- A GitHub Actions job (`.github/workflows/refresh.yml`) runs every 15 minutes, 07:30–23:59 IST, Mon–Sat.
- It reads new result filings from BSE and NSE, pulls EPS and market cap from Screener, applies the screen, and commits `docs/screen.json`.
- GitHub Pages serves `docs/` as the website; the page reloads data every minute.
- Run a refresh by hand: Actions → "Refresh EPS screen" → Run workflow.
