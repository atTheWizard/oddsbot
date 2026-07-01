"""
closing_lines_loop.py

Runs jobs.capture_closing_lines.run() on a fixed interval, forever. This
is the better fit for a long-running container (Railway "worker" service,
a VPS background process, docker-compose) compared to triggering it via
a separate cron entry every 10-15 minutes - one container just stays up
and loops.

If you're scheduling via Railway's cron jobs instead of a long-running
worker, you don't need this file - just point a Railway cron job
directly at jobs/capture_closing_lines.py on a 10-15 min schedule and
skip this loop.

Run with: python closing_lines_loop.py
"""

import time
from datetime import datetime

from jobs import capture_closing_lines

POLL_INTERVAL_SECONDS = 600  # 10 minutes


def main():
    print(f"[{datetime.now()}] Starting closing-line polling loop "
          f"(every {POLL_INTERVAL_SECONDS // 60} min)...")

    while True:
        try:
            capture_closing_lines.run()
        except Exception as e:
            # Log and keep looping - one bad poll (e.g. a transient API
            # error) shouldn't kill a long-running process. The next
            # iteration will retry naturally.
            print(f"[{datetime.now()}] Error during closing-line capture: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
