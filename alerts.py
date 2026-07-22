"""
Telegram Alerts
================
Sends you a Telegram message when the screener finds candidates.
Telegram is used because it's free, has a simple HTTP API, and push
notifications work well on mobile.

ONE-TIME SETUP
--------------
1. Open Telegram, message @BotFather, send /newbot, follow the
   prompts. You'll get a BOT_TOKEN (looks like 123456:ABC-xyz...).
2. Message your new bot anything (e.g. "hi") so it can message you back.
3. Get your CHAT_ID: visit this URL in a browser (replace TOKEN):
   https://api.telegram.org/botTOKEN/getUpdates
   Look for "chat":{"id": ...} in the JSON response - that number is
   your CHAT_ID.
4. Set both as environment variables (don't hardcode secrets in code):
     export VCP_TELEGRAM_BOT_TOKEN="123456:ABC-xyz..."
     export VCP_TELEGRAM_CHAT_ID="987654321"
   (Add these two lines to ~/.bashrc or your cron environment so they
   persist.)

USAGE
-----
    from alerts import send_telegram_alert
    send_telegram_alert("Hello from VCP screener!")
"""

import os
import requests

BOT_TOKEN = os.environ.get("VCP_TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("VCP_TELEGRAM_CHAT_ID")


def send_telegram_alert(message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[alerts] Telegram not configured (missing env vars) - skipping alert.")
        print("[alerts] See alerts.py docstring for setup steps.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"[alerts] Failed to send Telegram alert: {e}")
        return False


def format_candidates_message(df, scan_date: str) -> str:
    if df.empty:
        return f"*VCP Screener - {scan_date}*\nNo candidates found today."

    lines = [f"*VCP Screener - {scan_date}*", f"{len(df)} candidate(s) found:\n"]
    for _, row in df.iterrows():
        flags = []
        if row.get("VolumeContracting"):
            flags.append("vol-contracting")
        if row.get("NearRecentHigh"):
            flags.append("near-high")
        if row.get("Extended_AvoidNewEntry"):
            flags.append("⚠extended")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        ticker = row["Ticker"].replace(".NS", "")
        lines.append(
            f"• *{ticker}* @ {row['Close']} | "
            f"pullback {row['LastPullbackPct']}% | "
            f"+{row['PctAbove52WLow']}% off 52w low{flag_str}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    ok = send_telegram_alert("✅ VCP Screener alert system test - if you see this, it works!")
    print("Sent OK" if ok else "Failed - check your env vars")
