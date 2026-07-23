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
NEAR_BREAKOUT_PCT = 5.0
VOL_WINDOW = 15
MAX_EXTENSION_ABOVE_SMA50_PCT = 60.0

# Minimum length of a genuine consolidation/base, in trading days.
# ~10 trading days = 2 weeks, ~15 = 3 weeks - a real base needs to hold
# this long, not just a random 2-3 day dip.
MIN_BASE_DAYS = 10

# How tight the base must be: (highest high - lowest low) / lowest low
# over the base window, as a percentage. Minervini-style VCP bases
# are typically well under 20%; we use 15% as the default ceiling.
MAX_BASE_RANGE_PCT = 15.0

# Don't search back more than this many days when looking for the base
# (a base older than ~3-4 months isn't "the current" setup anymore).
BASE_LOOKBACK_CAP = 70

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
    # Migration: base_days was added later - add it to any DB created
    # before this change (e.g. one already committed to GitHub).
    try:
        conn.execute("ALTER TABLE scans ADD COLUMN base_days INTEGER")
    except sqlite3.OperationalError:
        pass  # column already exists
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
             sma10, sma20, sma50, sma200, score, details, base_days)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_date, row["Ticker"], row["Close"], row["PctAbove52WLow"],
            row["LastPullbackPct"], int(row["VolumeContracting"]),
            int(row["NearRecentHigh"]), int(row["Extended_AvoidNewEntry"]),
            row["SMA10"], row["SMA20"], row["SMA50"], row["SMA200"],
            row["Score"], row["Details"], row.get("BaseDays"),
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
    # Newer yfinance versions return MultiIndex columns like
    # (Close, RELIANCE.NS) even for a single-ticker download - flatten
    # them back to plain 'Close', 'Open', etc. so scalar access works.
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


def find_consolidation(df: pd.DataFrame):
    """
    Looks backward from the most recent trading day to find the longest
    genuine consolidation: a stretch of at least MIN_BASE_DAYS where
    price stayed within a tight range (<= MAX_BASE_RANGE_PCT).

    This directly implements "consolidation first for 2-3 weeks, THEN
    breakout" - rather than counting swing highs/lows, which produces
    false positives on choppy, wide-swinging stocks.

    Returns a dict with base_days, range_pct, base_high, base_low if a
    valid base is found, otherwise None.
    """
    if len(df) < MIN_BASE_DAYS:
        return None

    highs = df["High"]
    lows = df["Low"]
    max_lookback = min(len(df), BASE_LOOKBACK_CAP)

    best = None
    for window in range(MIN_BASE_DAYS, max_lookback + 1):
        seg_high = float(highs.iloc[-window:].max())
        seg_low = float(lows.iloc[-window:].min())
        range_pct = (seg_high - seg_low) / seg_low * 100
        if range_pct <= MAX_BASE_RANGE_PCT:
            best = {
                "base_days": window, "range_pct": round(range_pct, 2),
                "base_high": seg_high, "base_low": seg_low,
            }
        else:
            # Range only grows as the window widens, so once it blows
            # past the ceiling, further widening won't help - stop here.
            break

    return best


def check_breakout(df: pd.DataFrame, base: dict):
    """
    Given a confirmed base (from find_consolidation), checks whether
    price is at or near breaking out above the base's high.
    Returns (is_near_or_at_breakout: bool, pct_from_base_high: float)
    """
    close = float(df["Close"].iloc[-1])
    base_high = base["base_high"]
    pct_from_high = (base_high - close) / base_high * 100  # negative if already broken out
    is_near_or_at = pct_from_high <= NEAR_BREAKOUT_PCT
    return is_near_or_at, round(pct_from_high, 2)


def check_volume_contraction(df: pd.DataFrame, base_days: int = None) -> bool:
    """
    Compares average volume during the base window against the period
    right before it - true contraction means volume dried up DURING
    the base, which is what actually confirms supply is drying up.
    """
    window = base_days if base_days else VOL_WINDOW
    if len(df) < window * 2:
        return False
    base_vol = df["Volume"].tail(window).mean()
    prior_vol = df["Volume"].tail(window * 2).head(window).mean()
    return base_vol < prior_vol


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

        base = find_consolidation(df)
        if not base:
            return None

        is_near_breakout, pct_from_high = check_breakout(df, base)
        if not is_near_breakout:
            return None

        vol_contracting = check_volume_contraction(df, base_days=base["base_days"])
        extension_above_sma50 = (close - sma50) / sma50 * 100
        extended = extension_above_sma50 > MAX_EXTENSION_ABOVE_SMA50_PCT
        near_recent_high = pct_from_high <= NEAR_BREAKOUT_PCT

        details = (f"base: {base['base_days']} trading days, "
                   f"range {base['range_pct']}%, {pct_from_high}% from base high")

        return {
            "Ticker": ticker, "Close": round(close, 2),
            "PctAbove52WLow": round(pct_above_low, 1),
            "BaseDays": base["base_days"],
            "LastPullbackPct": base["range_pct"],
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
