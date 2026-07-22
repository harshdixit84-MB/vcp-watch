"""
VCP (Volatility Contraction Pattern) Screener - Core Logic
============================================================
Screens a given stock universe for Minervini-style VCP setups and
stores results in a local SQLite database (data/vcp.db).

Run standalone for a one-off scan:
    python3 screener.py

Used by run_daily.py for the automated daily job.
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "vcp.db"

# ----------------------------- CONFIG ---------------------------------
MIN_PCT_ABOVE_52W_LOW = 30.0
MAX_LAST_PULLBACK_PCT = 20.0
LOOKBACK_DAYS = 130
SWING_WINDOW = 5
NEAR_BREAKOUT_PCT = 5.0
VOL_WINDOW = 15
MAX_EXTENSION_ABOVE_SMA50_PCT = 60.0

# Fallback sample universe. In practice, load a real list via
# universe.load_universe_from_csv() - see universe.py
DEFAULT_STOCK_LIST = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "LT.NS", "SBIN.NS", "AXISBANK.NS", "BAJFINANCE.NS", "MARUTI.NS",
    "TITAN.NS", "SUNPHARMA.NS", "ULTRACEMCO.NS", "ADANIPORTS.NS",
]


# --------------------------- DATABASE ---------------------------------

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            close REAL,
            pct_above_52w_low REAL,
            last_pullback_pct REAL,
            volume_contracting INTEGER,
            near_recent_high INTEGER,
            extended_avoid_entry INTEGER,
            sma10 REAL, sma20 REAL, sma50 REAL, sma200 REAL,
            score REAL,
            details TEXT,
            UNIQUE(scan_date, ticker)
        )
    """)
    conn.commit()
    conn.close()


def save_results(df: pd.DataFrame, scan_date: str):
    if df.empty:
        return
    conn = sqlite3.connect(DB_PATH)
    for _, row in df.iterrows():
        conn.execute("""
            INSERT OR REPLACE INTO scans
            (scan_date, ticker, close, pct_above_52w_low, last_pullback_pct,
             volume_contracting, near_recent_high, extended_avoid_entry,
             sma10, sma20, sma50, sma200, score, details)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_date, row["Ticker"], row["Close"], row["PctAbove52WLow"],
            row["LastPullbackPct"], int(row["VolumeContracting"]),
            int(row["NearRecentHigh"]), int(row["Extended_AvoidNewEntry"]),
            row["SMA10"], row["SMA20"], row["SMA50"], row["SMA200"],
            row["Score"], row["Details"],
        ))
    conn.commit()
    conn.close()


def get_latest_scan_date():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT MAX(scan_date) FROM scans")
    result = cur.fetchone()[0]
    conn.close()
    return result


def get_results_for_date(scan_date: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM scans WHERE scan_date = ? ORDER BY score DESC",
        conn, params=(scan_date,)
    )
    conn.close()
    return df


def get_recent_scan_dates(limit: int = 30):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT DISTINCT scan_date FROM scans ORDER BY scan_date DESC LIMIT ?",
        (limit,)
    )
    dates = [r[0] for r in cur.fetchall()]
    conn.close()
    return dates


def get_ticker_history(ticker: str, limit: int = 60) -> pd.DataFrame:
    """All past scan appearances for one ticker - useful to see how long
    a stock has been showing up as a candidate."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT * FROM scans WHERE ticker = ? ORDER BY scan_date DESC LIMIT ?",
        conn, params=(ticker, limit)
    )
    conn.close()
    return df


def get_all_tickers():
    """Every distinct ticker that has ever appeared in a scan - used by
    build_site.py to know which per-ticker pages to generate."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT DISTINCT ticker FROM scans")
    tickers = [r[0] for r in cur.fetchall()]
    conn.close()
    return tickers


# --------------------------- SCREENING LOGIC ---------------------------

def fetch_data(ticker: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("Run: pip install yfinance pandas numpy --break-system-packages")
    df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
   if df.empty:
       return df
   if isinstance(df.columns, pd.MultiIndex):
       df.columns = df.columns.get_level_values(0)
   return df.dropna()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA10"] = df["Close"].rolling(10).mean()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["High52W"] = df["High"].rolling(252, min_periods=50).max()
    df["Low52W"] = df["Low"].rolling(252, min_periods=50).min()
    return df


def find_swings(series: pd.Series, window: int = SWING_WINDOW):
    highs, lows = [], []
    vals = series.values
    n = len(vals)
    for i in range(window, n - window):
        seg = vals[i - window:i + window + 1]
        if vals[i] == seg.max() and vals[i] != vals[i - 1]:
            highs.append(i)
        if vals[i] == seg.min() and vals[i] != vals[i - 1]:
            lows.append(i)
    return highs, lows


def analyze_contraction(df: pd.DataFrame):
    recent = df.tail(LOOKBACK_DAYS)
    if len(recent) < 40:
        return False, None, "not enough data"

    highs_idx, lows_idx = find_swings(recent["Close"], window=SWING_WINDOW)
    points = sorted([(i, "H") for i in highs_idx] + [(i, "L") for i in lows_idx])
    if len(points) < 3:
        return False, None, "not enough swing points found"

    pullbacks = []
    for j in range(len(points) - 1):
        idx1, typ1 = points[j]
        idx2, typ2 = points[j + 1]
        if typ1 == "H" and typ2 == "L":
            high_price = recent["Close"].iloc[idx1]
            low_price = recent["Close"].iloc[idx2]
            pullbacks.append(round((high_price - low_price) / high_price * 100, 2))

    if len(pullbacks) < 2:
        return False, None, "fewer than 2 pullbacks identified"

    last_pullback = pullbacks[-1]
    contracting = True
    for k in range(1, len(pullbacks)):
        if pullbacks[k] > pullbacks[k - 1] * 1.15:
            contracting = False
            break

    is_contracting = contracting and last_pullback <= MAX_LAST_PULLBACK_PCT
    return is_contracting, last_pullback, f"pullback sequence: {pullbacks}"


def check_volume_contraction(df: pd.DataFrame) -> bool:
    if len(df) < VOL_WINDOW * 2:
        return False
    recent_vol = df["Volume"].tail(VOL_WINDOW).mean()
    prior_vol = df["Volume"].tail(VOL_WINDOW * 2).head(VOL_WINDOW).mean()
    return recent_vol < prior_vol


def screen_stock(ticker: str):
    try:
        df = fetch_data(ticker)
        if df.empty or len(df) < 210:
            return None

        df = compute_indicators(df)
        last = df.iloc[-1]
        close = float(last["Close"])
        sma10, sma20, sma50, sma200 = (float(last["SMA10"]), float(last["SMA20"]),
                                        float(last["SMA50"]), float(last["SMA200"]))
        low52w, high52w = float(last["Low52W"]), float(last["High52W"])

        if any(pd.isna(x) for x in [sma10, sma20, sma50, sma200, low52w, high52w]):
            return None

        if not ((close > sma50 > sma200) and (close > sma20) and (close > sma10)):
            return None

        pct_above_low = (close - low52w) / low52w * 100
        if pct_above_low < MIN_PCT_ABOVE_52W_LOW:
            return None

        is_contracting, last_pullback, details = analyze_contraction(df)
        if not is_contracting:
            return None

        vol_contracting = check_volume_contraction(df)
        extension_above_sma50 = (close - sma50) / sma50 * 100
        extended = extension_above_sma50 > MAX_EXTENSION_ABOVE_SMA50_PCT
        recent_high_20d = float(df["Close"].tail(20).max())
        near_recent_high = (recent_high_20d - close) / recent_high_20d * 100 <= NEAR_BREAKOUT_PCT

        return {
            "Ticker": ticker, "Close": round(close, 2),
            "PctAbove52WLow": round(pct_above_low, 1),
            "LastPullbackPct": last_pullback,
            "VolumeContracting": vol_contracting,
            "NearRecentHigh": near_recent_high,
            "Extended_AvoidNewEntry": extended,
            "SMA10": round(sma10, 2), "SMA20": round(sma20, 2),
            "SMA50": round(sma50, 2), "SMA200": round(sma200, 2),
            "Details": details,
        }
    except Exception as e:
        print(f"  [skip] {ticker}: {e}")
        return None


def run_screener(tickers: list) -> pd.DataFrame:
    results = [r for r in (screen_stock(t) for t in tickers) if r]
    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results)
    out["Score"] = (
        out["VolumeContracting"].astype(int) * 2
        + out["NearRecentHigh"].astype(int) * 2
        + (~out["Extended_AvoidNewEntry"]).astype(int)
        + (20 - out["LastPullbackPct"]).clip(lower=0) / 5
    )
    return out.sort_values("Score", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    init_db()
    print(f"Running VCP screener on {len(DEFAULT_STOCK_LIST)} stocks...")
    results = run_screener(DEFAULT_STOCK_LIST)
    scan_date = datetime.now().strftime("%Y-%m-%d")

    if results.empty:
        print("No stocks matched the VCP criteria today.")
    else:
        save_results(results, scan_date)
        print(f"\n{len(results)} candidate(s) found and saved to {DB_PATH}\n")
        print(results.to_string(index=False))
