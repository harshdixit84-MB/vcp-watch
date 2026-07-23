"""
Static Site Builder
=====================
Renders the scan data in data/vcp.db into a folder of plain HTML files
(docs/), using the same templates as app.py, so the dashboard can be
published for free via GitHub Pages - no server required.

Run manually to preview:
    python3 build_site.py
    cd docs && python3 -m http.server 8000
    -> open http://localhost:8000

In production this is called automatically by run_daily.py inside the
GitHub Actions workflow (.github/workflows/daily-scan.yml).
"""

import shutil
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

import screener

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "docs"  # GitHub Pages serves from /docs on main branch

env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _date_links(all_dates, current_date, prefix):
    return [
        {"label": d[5:], "url": f"{prefix}date/{d}.html", "active": d == current_date}
        for d in all_dates
    ]


def _with_ticker_urls(records, prefix):
    for r in records:
        r["ticker_url"] = f"{prefix}ticker/{r['ticker']}.html"
        r["tradingview_url"] = f"https://www.tradingview.com/chart/?symbol=NSE:{r['ticker'].replace('.NS', '')}"
    return records


def build():
    screener.init_db()
    OUTPUT_DIR.mkdir(exist_ok=True)
    (OUTPUT_DIR / "date").mkdir(exist_ok=True)
    (OUTPUT_DIR / "ticker").mkdir(exist_ok=True)

    # refresh static assets (css etc.)
    dest_static = OUTPUT_DIR / "static"
    if dest_static.exists():
        shutil.rmtree(dest_static)
    shutil.copytree(STATIC_DIR, dest_static)

    all_dates = screener.get_recent_scan_dates(30)
    latest_date = screener.get_latest_scan_date()
    index_tpl = env.get_template("index.html")
    ticker_tpl = env.get_template("ticker.html")

    # --- root index.html = latest scan, prefix "" since it's at the root ---
    results = _with_ticker_urls(
        screener.get_results_for_date(latest_date).to_dict(orient="records") if latest_date else [],
        prefix="",
    )
    html = index_tpl.render(
        results=results, scan_date=latest_date,
        date_links=_date_links(all_dates, latest_date, prefix=""),
        css_url="static/style.css",
    )
    (OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")

    # --- one page per past date, in docs/date/, prefix "../" to get back to root ---
    for d in all_dates:
        results = _with_ticker_urls(
            screener.get_results_for_date(d).to_dict(orient="records"), prefix="../"
        )
        html = index_tpl.render(
            results=results, scan_date=d,
            date_links=_date_links(all_dates, d, prefix="../"),
            css_url="../static/style.css",
        )
        (OUTPUT_DIR / "date" / f"{d}.html").write_text(html, encoding="utf-8")

    # --- one page per ticker, in docs/ticker/ ---
    tickers = screener.get_all_tickers()
    for ticker in tickers:
        history = screener.get_ticker_history(ticker).to_dict(orient="records")
        symbol = ticker.replace(".NS", "")
        chart_json_path = OUTPUT_DIR / "chart-data" / f"{ticker}.json"
        html = ticker_tpl.render(
            ticker=ticker, history=history,
            home_url="../index.html", css_url="../static/style.css",
            tradingview_url=f"https://www.tradingview.com/chart/?symbol=NSE:{symbol}",
            chart_data_url=f"../chart-data/{ticker}.json",
            has_chart=chart_json_path.exists(),
        )
        (OUTPUT_DIR / "ticker" / f"{ticker}.html").write_text(html, encoding="utf-8")

    total = 1 + len(all_dates) + len(tickers)
    print(f"[build_site] Built {total} page(s) into {OUTPUT_DIR}")


if __name__ == "__main__":
    build()
