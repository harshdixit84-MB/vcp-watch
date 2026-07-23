"""
Chart Data Generator
======================
For each of today's candidate stocks, saves a compact JSON snapshot of
recent OHLC price data plus the detected consolidation zone (base high,
base low, base start date) so the dashboard can render an actual
candlestick chart showing exactly why the stock was flagged - not just
the summary numbers.

Called automatically by run_daily.py right after screening, for the
tickers that passed today. Output goes to docs/chart-data/<ticker>.json
(the same docs/ folder build_site.py publishes to GitHub Pages).
"""

import json
from pathlib import Path

import screener

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "docs" / "chart-data"
CHART_DAYS = 150  # how many recent trading days to include in the chart


def build_chart_json(ticker: str) -> bool:
    """
    Re-fetches recent price data for one ticker, re-detects its base
    (same logic as the main screen), and writes a JSON snapshot.
    Returns True on success, False if it couldn't be built.
    """
    try:
        df = screener.fetch_data(ticker)
        if df.empty or len(df) < screener.MIN_BASE_DAYS:
            return False

        window = max(CHART_DAYS, screener.BASE_LOOKBACK_CAP + 20)
        df = df.tail(window)

        base = screener.find_consolidation(df)
        if not base:
            return False

        base_start_date = df.index[-base["base_days"]].strftime("%Y-%m-%d")
        is_near_breakout, pct_from_high = screener.check_breakout(df, base)

        candles = df.tail(CHART_DAYS)
        records = [
            {
                "time": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
            }
            for idx, row in candles.iterrows()
        ]

        payload = {
            "ticker": ticker,
            "candles": records,
            "base": {
                "start_date": base_start_date,
                "high": round(base["base_high"], 2),
                "low": round(base["base_low"], 2),
                "days": base["base_days"],
                "range_pct": base["range_pct"],
            },
            "breakout": {
                "is_near_or_at": bool(is_near_breakout),
                "pct_from_base_high": pct_from_high,
            },
        }

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / f"{ticker}.json").write_text(json.dumps(payload), encoding="utf-8")
        return True
    except Exception as e:
        print(f"  [chart_data] skip {ticker}: {e}")
        return False


def build_all(tickers) -> int:
    ok = 0
    for t in tickers:
        if build_chart_json(t):
            ok += 1
    print(f"[chart_data] Built {ok}/{len(tickers)} chart snapshot(s)")
    return ok


if __name__ == "__main__":
    # quick manual test against a single ticker
    print(build_chart_json("RELIANCE.NS"))
