# VCP Watch — Personal Stock Screener

A daily screener that flags NSE stocks forming a Volatility Contraction
Pattern (Minervini-style), stores results, alerts you on Telegram, and
publishes a small dashboard — all running for free on GitHub (Actions
for the daily job, Pages for the website, no server to pay for or
maintain).

## Project layout

```
vcp_project/
├── screener.py       # core screening logic + SQLite storage
├── universe.py        # loads your stock list (Nifty 500 etc.)
├── alerts.py           # Telegram notifications
├── run_daily.py         # the one script cron calls
├── app.py                 # Flask dashboard (the "webpage")
├── templates/               # dashboard HTML
├── static/style.css           # dashboard design
├── data/                        # SQLite DB + your universe CSV live here
└── requirements.txt
```

## 1. One-time setup

```bash
cd vcp_project
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Get your real stock universe: download the Nifty 500 constituents CSV
from niftyindices.com or your broker, save it as `data/nifty500.csv`
with a `Symbol` column. Without this file the screener falls back to a
14-stock sample so you can still test everything end-to-end.

Set up Telegram alerts (optional but recommended — see full steps in
`alerts.py`):

```bash
export VCP_TELEGRAM_BOT_TOKEN="your-bot-token"
export VCP_TELEGRAM_CHAT_ID="your-chat-id"
```

Add those two lines to `~/.bashrc` (or wherever your cron job's shell
reads env vars from) so they persist.

## 2. Test it manually

```bash
python3 run_daily.py
```

This screens your universe, saves matches to `data/vcp.db`, and sends
a Telegram alert. Run it a few times on different days to build up
history.

## 3. Run the dashboard

```bash
python3 app.py
```

Open http://localhost:5000 — you'll see today's candidates, a date
strip to browse past scans, and click-through per-ticker history.

## 4. Automate the daily scan (cron)

```bash
crontab -e
```

Add (runs 4:30 PM every weekday, after NSE close at 3:30 PM):

```
30 16 * * 1-5 cd /full/path/to/vcp_project && /full/path/to/venv/bin/python3 run_daily.py >> logs/cron.log 2>&1
```

Create the log folder first: `mkdir -p logs`

## 5. Deploy for free — GitHub Actions + GitHub Pages

No server needed. Two GitHub features do all the work:

- **GitHub Actions** runs `run_daily.py` on a schedule (free, unlimited
  minutes on public repos) — this is your "cron in the cloud".
- **GitHub Pages** serves the `docs/` folder as a live website for
  free — this is your dashboard.

`build_site.py` renders the same dashboard templates into plain HTML
files inside `docs/` (no live server involved), and `run_daily.py`
calls it automatically after every scan. The workflow file
`.github/workflows/daily-scan.yml` is already set up to run the scan
and commit the results back to the repo each weekday.

Setup is: push this repo to GitHub → add your Telegram credentials as
repo Secrets → enable Pages pointing at the `docs/` folder → done.
(Full click-by-click steps provided separately.)

`app.py` (Flask) still exists in this project as an *optional* way to
preview the dashboard live on your own machine (`python3 app.py`) —
it's not needed for the GitHub deployment, since `build_site.py`
covers that.

## Notes / honest limitations

- This is a heuristic pattern detector, not a certified indicator —
  it will produce false positives and miss valid setups. Treat it as
  a daily shortlist to eyeball, not a buy signal.
- yfinance data can lag or have gaps; for anything beyond casual
  personal use, a paid data feed or your broker's API is more
  reliable.
- No order execution is included. If you want to wire this to actual
  order placement later (e.g. via Zerodha Kite Connect), that's a
  separate, higher-stakes step — happy to help when you're ready, but
  it deserves its own careful pass (risk limits, kill-switch, etc.)
  rather than being bolted onto the screener.
