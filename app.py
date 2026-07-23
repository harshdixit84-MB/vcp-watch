"""
VCP Screener Dashboard - Flask Web App
========================================
A simple personal webpage showing your daily VCP candidates, with
history so you can see how long a stock has been showing up.

RUN LOCALLY (for testing)
--------------------------
    pip install flask --break-system-packages
    python3 app.py
    -> open http://localhost:5000

RUN FOR REAL (always-on, on your VM)
--------------------------------------
Use a production server instead of Flask's dev server:
    pip install gunicorn --break-system-packages
    gunicorn -w 2 -b 0.0.0.0:8000 app:app

Then optionally put Nginx in front of it for a real domain + HTTPS
(see deployment notes at the bottom of README.md).
"""

from flask import Flask, render_template, jsonify, send_from_directory
import screener

app = Flask(__name__)
DOCS_DIR = screener.BASE_DIR / "docs"


def _date_links(all_dates, current_date):
    return [
        {"label": d[5:], "url": f"/date/{d}", "active": d == current_date}
        for d in all_dates
    ]


def _with_ticker_urls(records):
    for r in records:
        r["ticker_url"] = f"/ticker/{r['ticker']}"
        r["tradingview_url"] = f"https://www.tradingview.com/chart/?symbol=NSE:{r['ticker'].replace('.NS', '')}"
    return records


@app.route("/")
def index():
    latest_date = screener.get_latest_scan_date()
    all_dates = screener.get_recent_scan_dates(30)
    if not latest_date:
        return render_template("index.html", results=[], scan_date=None,
                                date_links=[], css_url="/static/style.css")

    results = _with_ticker_urls(screener.get_results_for_date(latest_date).to_dict(orient="records"))
    return render_template(
        "index.html", results=results, scan_date=latest_date,
        date_links=_date_links(all_dates, latest_date), css_url="/static/style.css",
    )


@app.route("/date/<scan_date>")
def by_date(scan_date):
    results = _with_ticker_urls(screener.get_results_for_date(scan_date).to_dict(orient="records"))
    all_dates = screener.get_recent_scan_dates(30)
    return render_template(
        "index.html", results=results, scan_date=scan_date,
        date_links=_date_links(all_dates, scan_date), css_url="/static/style.css",
    )


@app.route("/api/latest")
def api_latest():
    """JSON endpoint - useful if you later want a phone widget or
    another tool to consume this data."""
    latest_date = screener.get_latest_scan_date()
    if not latest_date:
        return jsonify({"scan_date": None, "results": []})
    results = screener.get_results_for_date(latest_date)
    return jsonify({
        "scan_date": latest_date,
        "results": results.to_dict(orient="records"),
    })


@app.route("/ticker/<ticker>")
def ticker_history(ticker):
    history = screener.get_ticker_history(ticker)
    symbol = ticker.replace(".NS", "")
    chart_json_path = DOCS_DIR / "chart-data" / f"{ticker}.json"
    return render_template(
        "ticker.html", ticker=ticker, history=history.to_dict(orient="records"),
        home_url="/", css_url="/static/style.css",
        tradingview_url=f"https://www.tradingview.com/chart/?symbol=NSE:{symbol}",
        chart_data_url=f"/chart-data/{ticker}.json",
        has_chart=chart_json_path.exists(),
    )


@app.route("/chart-data/<path:filename>")
def chart_data_file(filename):
    return send_from_directory(DOCS_DIR / "chart-data", filename)


if __name__ == "__main__":
    screener.init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
