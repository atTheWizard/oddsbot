"""
run_daily.py

Chains the four daily steps into a single command, so any scheduler
(Railway cron, a VPS crontab, GitHub Actions) only needs to trigger one
thing instead of four separate commands in the right order.

Stops early and exits non-zero if any step raises, so a failed ingestion
doesn't silently let scoring run on stale/missing data.

Run with: python run_daily.py
Or inside Docker: docker run <image> python run_daily.py
"""

import sys
from datetime import datetime

from config import BOOKMAKER_KEY
from jobs import ingest_odds, run_scoring, select_picks
from bot import telegram_bot


def main():
    steps = [
        ("Ingest odds", lambda: ingest_odds.run(bookmaker_key=BOOKMAKER_KEY)),
        ("Run scoring", run_scoring.run),
        ("Select picks", select_picks.run),
        ("Send Telegram digest", telegram_bot.send_daily_digest),
    ]

    for name, fn in steps:
        print(f"\n[{datetime.now()}] === {name} ===")
        try:
            fn()
        except Exception as e:
            print(f"[{datetime.now()}] FAILED at step '{name}': {e}")
            sys.exit(1)

    print(f"\n[{datetime.now()}] Daily pipeline complete.")


if __name__ == "__main__":
    main()
