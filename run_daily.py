"""
Daily Runner - THIS is the script your cron job / scheduler calls.
=====================================================================
Loads the full universe, runs the screener, saves to DB, sends a
Telegram alert. One command, fully automated.

    python3 run_daily.py
"""

from datetime import datetime
import screener
import universe
import alerts
import build_site
import chart_data


def main():
    screener.init_db()

    tickers = universe.load_universe_from_csv()
    print(f"[run_daily] Screening {len(tickers)} tickers...")

    results = screener.run_screener(tickers)
    scan_date = datetime.now().strftime("%Y-%m-%d")

    if not results.empty:
        screener.save_results(results, scan_date)
        print(f"[run_daily] {len(results)} candidate(s) saved for {scan_date}")
        chart_data.build_all(results["Ticker"].tolist())
    else:
        print(f"[run_daily] No candidates found for {scan_date}")

    message = alerts.format_candidates_message(results, scan_date)
    alerts.send_telegram_alert(message)

    build_site.build()
    print("[run_daily] Done.")


if __name__ == "__main__":
    main()
