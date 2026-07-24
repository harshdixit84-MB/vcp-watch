"""
VCP (Volatility Contraction Pattern) Screener - Core Logic
============================================================
Screens a given stock universe for genuine VCP setups and stores
results in a local SQLite database (data/vcp.db).

Detection flow (institutional-style, not a fixed-window heuristic):
    Stage 2 trend template
      -> Swing high / swing low detection (percentage zigzag)
      -> Measure pullback sequence between swings
      -> Verify pullbacks are genuinely contracting (e.g. 25% -> 16% -> 9% -> 5%)
      -> ATR contraction (volatility shrinking)
      -> Volume contraction + dry-up near pivot + breakout expansion
      -> Range contraction
      -> Identify pivot (base high)
      -> Check breakout / near-breakout
      -> Relative strength vs index (soft signal, contributes to score)

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

# --- Stage 2 trend template ---
MIN_PCT_ABOVE_52W_LOW = 30.0     # must be well clear of the 52-week low
NEAR_52W_HIGH_PCT = 25.0         # within this % of the 52-week high (Minervini trend template)
SMA_RISING_LOOKBACK = 20         # days back to compare SMA50/SMA200 slope
MAX_EXTENSION_ABOVE_SMA50_PCT = 60.0  # too far above SMA50 = late/risky entry

# --- Swing detection (zigzag) ---
ZIGZAG_PCT = 4.0                 # min % reversal to register a new swing point
MIN_PULLBACKS = 2                # need at least this many H->L legs to call it VCP
PULLBACK_CONTRACTION_TOLERANCE = 1.15  # allow up to 15% noise; must shrink beyond this
FINAL_PULLBACK_MAX_PCT = 12.0    # the pullback nearest the pivot must be this tight
SWING_LOOKBACK_CAP = 130         # don't search back more than ~6 months for the base

# --- Breakout / pivot ---
NEAR_BREAKOUT_PCT = 5.0          # within this % of pivot counts as "near breakout"

# --- Relative strength vs index (soft signal) ---
BENCHMARK_TICKER = "^NSEI"       # Nifty 50 index
RS_NEAR_HIGH_PCT = 15.0          # RS line within this % of its own recent high

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
            base_days INTEGER,
            num_pullbacks INTEGER,
            pivot_price REAL,
            atr_contracting INTEGER,
            volume_dryup INTEGER,
            breakout_vol_expansion INTEGER,
            rs_near_high INTEGER,
            UNIQUE(scan_date, ticker)
        )
    """)
    # Migrations for columns added after the table was first created -
    # safe to re-run; each is a no-op if the column already exists.
    for col_def in [
        "base_days INTEGER", "num_pullbacks INTEGER", "pivot_price REAL",
        "atr_contracting INTEGER", "volume_dryup INTEGER",
        "breakout_vol_expansion INTEGER", "rs_near_high INTEGER",
    ]:
        try:
            conn.execute(f"ALTER TABLE scans ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass
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
             sma10, sma20, sma50, sma200, score, details, base_days,
             num_pullbacks, pivot_price, atr_contracting, volume_dryup,
             breakout_vol_expansion, rs_near_high)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            scan_date, row["Ticker"], row["Close"], row["PctAbove52WLow"],
            row["LastPullbackPct"], int(row["VolumeContracting"]),
            int(row["NearRecentHigh"]), int(row["Extended_AvoidNewEntry"]),
            row["SMA10"], row["SMA20"], row["SMA50"], row["SMA200"],
            row["Score"], row["Details"], row.get("BaseDays"),
            row.get("NumPullbacks"), row.get("PivotPrice"),
            int(row.get("AtrContracting", False)), int(row.get("VolumeDryup", False)),
            int(row.get("BreakoutVolExpansion", False)), int(row.get("RsNearHigh", False)),
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


# --------------------------- DATA FETCHING ---------------------------

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


_benchmark_cache = {}


def get_benchmark_data():
    """Fetches and caches the index benchmark (Nifty 50) for relative
    strength comparisons - fetched once per run, not once per stock."""
    if BENCHMARK_TICKER not in _benchmark_cache:
        try:
            _benchmark_cache[BENCHMARK_TICKER] = fetch_data(BENCHMARK_TICKER)
        except Exception:
            _benchmark_cache[BENCHMARK_TICKER] = pd.DataFrame()
    return _benchmark_cache[BENCHMARK_TICKER]
    # --------------------------- INDICATORS ---------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["SMA10"] = df["Close"].rolling(10).mean()
    df["SMA20"] = df["Close"].rolling(20).mean()
    df["SMA50"] = df["Close"].rolling(50).mean()
    df["SMA150"] = df["Close"].rolling(150).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["High52W"] = df["High"].rolling(252, min_periods=50).max()
    df["Low52W"] = df["Low"].rolling(252, min_periods=50).min()
    return df


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# --------------------------- STAGE 2 TREND TEMPLATE ---------------------------

def check_stage2_trend(df: pd.DataFrame) -> tuple:
    """
    Expanded Minervini-style trend template:
      - Close > SMA50 > SMA150 > SMA200 (proper stacked order)
      - SMA50 and SMA200 both rising (not just above, actually trending up)
      - Within NEAR_52W_HIGH_PCT of the 52-week high
      - At least MIN_PCT_ABOVE_52W_LOW above the 52-week low
    Returns (passes: bool, info: dict) with the raw values for scoring/display.
    """
    if len(df) < 200 + SMA_RISING_LOOKBACK:
        return False, {}

    last = df.iloc[-1]
    close = float(last["Close"])
    sma50, sma150, sma200 = float(last["SMA50"]), float(last["SMA150"]), float(last["SMA200"])
    low52w, high52w = float(last["Low52W"]), float(last["High52W"])

    if any(pd.isna(x) for x in [sma50, sma150, sma200, low52w, high52w]):
        return False, {}

    stacked_order = close > sma50 > sma150 > sma200

    sma50_prior = float(df["SMA50"].iloc[-1 - SMA_RISING_LOOKBACK])
    sma200_prior = float(df["SMA200"].iloc[-1 - SMA_RISING_LOOKBACK])
    # SMA50 can lag briefly during a tightening base (still digesting an
    # earlier pullback) even in a genuinely healthy uptrend - allow a
    # small tolerance rather than requiring strict day-over-day rise.
    sma50_rising = sma50 >= sma50_prior * 0.995
    sma200_rising = sma200 > sma200_prior

    pct_above_low = (close - low52w) / low52w * 100
    pct_from_high = (high52w - close) / high52w * 100

    passes = (
        stacked_order and sma50_rising and sma200_rising
        and pct_above_low >= MIN_PCT_ABOVE_52W_LOW
        and pct_from_high <= NEAR_52W_HIGH_PCT
    )

    return passes, {
        "close": close, "sma50": sma50, "sma150": sma150, "sma200": sma200,
        "pct_above_low": round(pct_above_low, 1),
        "pct_from_high": round(pct_from_high, 1),
        "sma50_rising": sma50_rising, "sma200_rising": sma200_rising,
    }


# --------------------------- SWING / CONTRACTION DETECTION ---------------------------

def zigzag_swings(df: pd.DataFrame, pct_threshold: float = ZIGZAG_PCT):
    """
    Percentage-based zigzag: a new swing point is confirmed only when
    price reverses by at least pct_threshold% from the running extreme.
    This adapts to each stock's own volatility instead of using a fixed
    lookback window, so it correctly finds real swing highs/lows on
    both quiet and volatile stocks.

    Returns a list of (index_position, price, 'H'|'L') in chronological order.
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)
    if n < 5:
        return []

    swings = []
    direction = None
    running_high_idx, running_high = 0, highs[0]
    running_low_idx, running_low = 0, lows[0]

    for i in range(1, n):
        if direction is None:
            up_move = (highs[i] - lows[0]) / lows[0] * 100
            down_move = (highs[0] - lows[i]) / highs[0] * 100
            if up_move >= pct_threshold:
                direction = "up"
                swings.append((0, lows[0], "L"))
                running_high_idx, running_high = i, highs[i]
            elif down_move >= pct_threshold:
                direction = "down"
                swings.append((0, highs[0], "H"))
                running_low_idx, running_low = i, lows[i]
            continue

        if direction == "up":
            if highs[i] > running_high:
                running_high, running_high_idx = highs[i], i
            drop_pct = (running_high - lows[i]) / running_high * 100
            if drop_pct >= pct_threshold:
                swings.append((running_high_idx, running_high, "H"))
                direction = "down"
                running_low, running_low_idx = lows[i], i
        else:
            if lows[i] < running_low:
                running_low, running_low_idx = lows[i], i
            rise_pct = (highs[i] - running_low) / running_low * 100
            if rise_pct >= pct_threshold:
                swings.append((running_low_idx, running_low, "L"))
                direction = "up"
                running_high, running_high_idx = highs[i], i

    if direction == "up":
        swings.append((running_high_idx, running_high, "H"))
    elif direction == "down":
        swings.append((running_low_idx, running_low, "L"))
    return swings


def get_pullback_sequence(swings, cutoff_idx: int = 0):
    """
    Extracts H->L pullback percentages from a swing list, in chronological
    order, restricted to swings at or after cutoff_idx (so we only look
    at the CURRENT base-building phase, not the whole 2-year history).
    Returns list of dicts: {pct, high_idx, high_price, low_idx, low_price}
    """
    relevant = [s for s in swings if s[0] >= cutoff_idx]
    pullbacks = []
    for j in range(len(relevant) - 1):
        idx1, p1, t1 = relevant[j]
        idx2, p2, t2 = relevant[j + 1]
        if t1 == "H" and t2 == "L":
            pullbacks.append({
                "pct": round((p1 - p2) / p1 * 100, 2),
                "high_idx": idx1, "high_price": p1,
                "low_idx": idx2, "low_price": p2,
            })
    return pullbacks


def verify_contraction(pullbacks: list):
    """
    Each pullback must be meaningfully smaller than the one before it
    (allowing some noise via PULLBACK_CONTRACTION_TOLERANCE), and the
    FINAL pullback (nearest the pivot) must be tight - this is what
    actually defines "ready to break out", not just "somewhat contracting".
    Returns (verified: bool, reason: str)
    """
    if len(pullbacks) < MIN_PULLBACKS:
        return False, f"only {len(pullbacks)} pullback(s) found, need >= {MIN_PULLBACKS}"

    pcts = [p["pct"] for p in pullbacks]
    for k in range(1, len(pcts)):
        if pcts[k] > pcts[k - 1] * PULLBACK_CONTRACTION_TOLERANCE:
            return False, f"pullback {k+1} ({pcts[k]}%) did not contract vs previous ({pcts[k-1]}%)"

    if pcts[-1] > FINAL_PULLBACK_MAX_PCT:
        return False, f"final pullback {pcts[-1]}% exceeds tight-base ceiling {FINAL_PULLBACK_MAX_PCT}%"

    return True, "contracting sequence confirmed"


def check_atr_contraction(df: pd.DataFrame, base_start_idx: int) -> bool:
    """Volatility (ATR) should be shrinking through the base, not just price range."""
    atr = compute_atr(df)
    if atr.iloc[base_start_idx:].isna().all():
        return False
    early = atr.iloc[base_start_idx: base_start_idx + 10].mean()
    recent = atr.iloc[-10:].mean()
    if pd.isna(early) or pd.isna(recent):
        return False
    return recent < early


def check_volume_profile(df: pd.DataFrame, pullbacks: list, base_start_idx: int) -> dict:
    """
    Real VCP volume analysis:
      - average volume during the base lower than before it
      - the lowest-volume days cluster near the final pullback / pivot
      - volume expands if a breakout has already occurred
    """
    volume = df["Volume"]
    n = len(df)

    prior_period = volume.iloc[max(0, base_start_idx - 20):base_start_idx]
    base_period = volume.iloc[base_start_idx:]
    avg_contracting = bool(base_period.mean() < prior_period.mean()) if len(prior_period) else False
    median_contracting = bool(base_period.median() < prior_period.median()) if len(prior_period) else False

    dryup_near_pivot = False
    if pullbacks:
        final_pb = pullbacks[-1]
        final_leg = volume.iloc[final_pb["high_idx"]:final_pb["low_idx"] + 1]
        rest_of_base = volume.iloc[base_start_idx:final_pb["high_idx"]]
        if len(final_leg) and len(rest_of_base):
            dryup_near_pivot = bool(final_leg.mean() < rest_of_base.mean())

    # Breakout volume expansion: only meaningful if price has actually
    # closed above the pivot already (checked by caller via is_breakout)
    recent_vol = volume.iloc[-3:].mean()
    base_avg_vol = base_period.mean() if len(base_period) else np.nan
    breakout_expansion = bool(recent_vol > base_avg_vol) if not pd.isna(base_avg_vol) else False

    return {
        "volume_dryup": avg_contracting and median_contracting and dryup_near_pivot,
        "breakout_vol_expansion": breakout_expansion,
    }


def check_relative_strength(df: pd.DataFrame) -> bool:
    """
    Soft signal: is this stock's price-relative-to-index (RS) line near
    its own recent high? That means the stock is outperforming the
    index, not just rising with the broader market.
    """
    try:
        benchmark = get_benchmark_data()
        if benchmark.empty or len(benchmark) < len(df):
            return False
        bench_aligned = benchmark["Close"].reindex(df.index).ffill()
        rs_line = df["Close"] / bench_aligned
        rs_line = rs_line.dropna()
        if len(rs_line) < 60:
            return False
        rs_recent = rs_line.iloc[-1]
        rs_high = rs_line.tail(252).max()
        pct_from_rs_high = (rs_high - rs_recent) / rs_high * 100
        return pct_from_rs_high <= RS_NEAR_HIGH_PCT
    except Exception:
        return False


def find_vcp_base(df: pd.DataFrame):
    """
    Full base-detection pipeline: finds swings, measures pullbacks,
    verifies genuine contraction, and identifies the pivot (base high).
    Returns a dict describing the base, or None if no valid VCP base
    is currently forming.
    """
    lookback_df = df.tail(SWING_LOOKBACK_CAP)
    offset = len(df) - len(lookback_df)

    swings = zigzag_swings(lookback_df, pct_threshold=ZIGZAG_PCT)
    if len(swings) < 3:
        return None

    pullbacks = get_pullback_sequence(swings)
    if not pullbacks:
        return None

    verified, reason = verify_contraction(pullbacks)
    if not verified:
        return None

    # Swing indices from zigzag_swings are local to lookback_df (the
    # truncated tail) - convert to global positions in the FULL df now,
    # before they're used to slice full-length series like Volume/ATR.
    for p in pullbacks:
        p["high_idx"] += offset
        p["low_idx"] += offset

    final_pb = pullbacks[-1]
    pivot_price = final_pb["high_price"]
    base_start_idx_global = pullbacks[0]["high_idx"]
    base_days = len(df) - base_start_idx_global

    if base_days < 10:  # ~2 weeks minimum, mirrors the "2-3 week consolidation" requirement
        return None

    atr_contracting = check_atr_contraction(df, base_start_idx_global)
    vol_profile = check_volume_profile(df, pullbacks, base_start_idx_global)
    rs_near_high = check_relative_strength(df)

    return {
        "pivot_price": pivot_price,
        "base_start_idx": base_start_idx_global,
        "base_start_date": df.index[base_start_idx_global],
        "base_days": base_days,
        "num_pullbacks": len(pullbacks),
        "pullback_pcts": [p["pct"] for p in pullbacks],
        "final_pullback_pct": final_pb["pct"],
        "atr_contracting": atr_contracting,
        "volume_dryup": vol_profile["volume_dryup"],
        "breakout_vol_expansion": vol_profile["breakout_vol_expansion"],
        "rs_near_high": rs_near_high,
    }


def check_breakout(df: pd.DataFrame, base: dict):
    """Checks whether price is at or near breaking out above the pivot."""
    close = float(df["Close"].iloc[-1])
    pivot = base["pivot_price"]
    pct_from_pivot = (pivot - close) / pivot * 100  # negative if already broken out
    is_near_or_at = pct_from_pivot <= NEAR_BREAKOUT_PCT
    return is_near_or_at, round(pct_from_pivot, 2)


# --------------------------- MAIN SCREENING ---------------------------

def screen_stock(ticker: str):
    try:
        df = fetch_data(ticker)
        if df.empty or len(df) < 220:
            return None

        df = compute_indicators(df)

        stage2_ok, trend_info = check_stage2_trend(df)
        if not stage2_ok:
            return None

        base = find_vcp_base(df)
        if not base:
            return None

        is_near_breakout, pct_from_pivot = check_breakout(df, base)
        if not is_near_breakout:
            return None

        close = trend_info["close"]
        extension_above_sma50 = (close - trend_info["sma50"]) / trend_info["sma50"] * 100
        extended = extension_above_sma50 > MAX_EXTENSION_ABOVE_SMA50_PCT

        details = (
            f"{base['num_pullbacks']} pullbacks {base['pullback_pcts']}, "
            f"base {base['base_days']}d, pivot {round(base['pivot_price'], 2)}, "
            f"{pct_from_pivot}% from pivot"
        )

        return {
            "Ticker": ticker, "Close": round(close, 2),
            "PctAbove52WLow": trend_info["pct_above_low"],
            "BaseDays": base["base_days"],
            "NumPullbacks": base["num_pullbacks"],
            "PivotPrice": round(base["pivot_price"], 2),
            "LastPullbackPct": base["final_pullback_pct"],
            "VolumeContracting": base["volume_dryup"],
            "VolumeDryup": base["volume_dryup"],
            "AtrContracting": base["atr_contracting"],
            "BreakoutVolExpansion": base["breakout_vol_expansion"],
            "RsNearHigh": base["rs_near_high"],
            "NearRecentHigh": is_near_breakout,
            "Extended_AvoidNewEntry": extended,
            "SMA10": round(float(df["SMA10"].iloc[-1]), 2),
            "SMA20": round(float(df["SMA20"].iloc[-1]), 2),
            "SMA50": round(trend_info["sma50"], 2),
            "SMA200": round(trend_info["sma200"], 2),
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
    # Score weights hard-pass signals plus the soft confirming signals
    # (ATR contraction, RS strength, breakout volume expansion) so the
    # ranking reflects setup quality, not just pass/fail.
    out["Score"] = (
        out["VolumeContracting"].astype(int) * 2
        + out["AtrContracting"].astype(int) * 1.5
        + out["RsNearHigh"].astype(int) * 1.5
        + out["BreakoutVolExpansion"].astype(int) * 1
        + out["NearRecentHigh"].astype(int) * 1
        + (~out["Extended_AvoidNewEntry"]).astype(int)
        + (12 - out["LastPullbackPct"]).clip(lower=0) / 3
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
