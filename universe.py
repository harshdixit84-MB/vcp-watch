"""
Universe Loader
================
Provides the full list of stocks to screen, instead of a hand-typed
sample. Two options:

OPTION A (recommended): Download the official NSE index constituents
CSV once, save it locally, and load from that.
    1. Go to: https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-500
       (or nseindia.com) and download the "Nifty 500 List" CSV.
    2. Save it as data/nifty500.csv in this project folder.
    3. It should have a 'Symbol' column with raw NSE symbols (e.g. RELIANCE).
    4. This script will append '.NS' automatically for yfinance.

OPTION B: If you use a broker (Zerodha, Upstox, etc.), most let you
export your watchlist or the full instrument list as CSV - same idea,
just point load_universe_from_csv() at that file with the right
column name.

Re-download/update this CSV every few months since index constituents
change periodically.
"""

import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CSV_PATH = BASE_DIR / "data" / "nifty500.csv"


def load_universe_from_csv(path=DEFAULT_CSV_PATH, symbol_col: str = "Symbol", suffix: str = ".NS"):
    """
    Load tickers from a locally saved constituents CSV.
    Falls back to a small sample list with a warning if the file isn't found,
    so the rest of the pipeline still runs for testing.
    """
    path = Path(path)
    if not path.exists():
        print(f"[universe] WARNING: {path} not found. "
              f"Using a small sample list instead - see universe.py docstring "
              f"for how to get the real Nifty 500 list.")
        from screener import DEFAULT_STOCK_LIST
        return DEFAULT_STOCK_LIST

    df = pd.read_csv(path)
    if symbol_col not in df.columns:
        raise ValueError(
            f"Column '{symbol_col}' not found in {path}. "
            f"Available columns: {list(df.columns)}"
        )
    tickers = [f"{str(s).strip()}{suffix}" for s in df[symbol_col].dropna().tolist()]
    print(f"[universe] Loaded {len(tickers)} tickers from {path}")
    return tickers


if __name__ == "__main__":
    tickers = load_universe_from_csv()
    print(tickers[:10], "..." if len(tickers) > 10 else "")
