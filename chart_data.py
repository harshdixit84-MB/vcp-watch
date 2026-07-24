"""
Chart Data Generator
======================
For each of today's candidate stocks, saves a compact JSON snapshot of
recent OHLC price data plus every detected swing high/low and the pivot
- so the dashboard can render a candlestick chart that visually marks
exactly which contraction pattern got the stock flagged, not just a
summary number.

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
    using the same swing/contraction logic as the main screen, and
    writes a JSON snapshot with every swing point marked. Returns True
    on success, False if it couldn't be built.
    """
    try:
        df = screener.fetch_data(ticker)
        if df.empty or len(df) < 220:
            return False

        base = screener.find_vcp_base(df)
        if not base:
            return False

        # Re-derive the same swing points find_vcp_base used internally,
        # so the chart shows exactly what the algorithm saw.
        lookback_df = df.tail(screener.SWING_LOOKBACK_CAP)
        offset = len(df) - len(lookback_df)
        swings = screener.zigzag_swings(lookback_df, pct_threshold=screener.ZIGZAG_PCT)
        swings_global = [(idx + offset, price, typ) for idx, price, typ in swings]
        base_start_idx = base["base_start_idx"]
        relevant_swings = [s for s in swings_global if s[0] >= base_start_idx]

        swing_markers = [
            {
                "time": df.index[idx].strftime("%Y-%m-%d"),
                "price": round(float(price), 2),
                "type": typ,
            }
            for idx, price, typ in relevant_swings
        ]

        is_near_breakout, pct_from_pivot = screener.check_breakout(df, base)

        window = max(CHART_DAYS, screener.SWING_LOOKBACK_CAP + 20)
        candles = df.tail(window)
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
            "swings": swing_markers,
            "base": {
                "start_date": base["base_start_date"].strftime("%Y-%m-%d"),
                "pivot": round(base["pivot_price"], 2),
                "days": base["base_days"],
                "num_pullbacks": base["num_pullbacks"],
                "pullback_pcts": base["pullback_pcts"],
            },
            "breakout": {
                "is_near_or_at": bool(is_near_breakout),
                "pct_from_pivot": pct_from_pivot,
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
    print(build_chart_json("RELIANCE.NS"))
