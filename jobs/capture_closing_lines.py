"""
jobs/capture_closing_lines.py

Unlike the other jobs, this one does NOT run once a day at a fixed time.
It needs to run frequently (e.g. every 10-15 minutes via cron) and each
run checks: "which picks have a fixture kicking off soon, and don't yet
have a closing_odds value recorded?" This is the per-match timing piece
discussed in the system design - rather than scheduling one job per
match, we poll frequently and act when a match enters the capture window.

For each matching pick, fetches the current odds one more time (this
becomes the closing line) and computes CLV using model.ev_scoring.compute_clv.

Run manually with: python -m jobs.capture_closing_lines
Recommended schedule: every 10-15 minutes, all day.
"""

from datetime import datetime, timedelta

from config import CLOSING_LINE_WINDOW_MINUTES
from db.connection import get_cursor
from model.ev_scoring import compute_clv
from jobs.ingest_odds import fetch_odds_from_api, parse_api_response


def get_picks_needing_closing_line(cur, window_minutes: int) -> list[dict]:
    """
    Finds picks where:
    - the fixture kicks off within window_minutes from now
    - closing_odds has not been captured yet

    This is intentionally a window (not an exact instant) since this job
    runs periodically, not continuously - a 30 minute window with a
    15 minute polling interval guarantees every pick gets caught at least
    once before kickoff, usually twice.
    """
    cur.execute(
        """
        SELECT p.pick_id, p.fixture_id, p.outcome, p.your_odds, f.external_id,
               f.kickoff_time, ht.name as home_team, at.name as away_team
        FROM picks p
        JOIN fixtures f ON f.fixture_id = p.fixture_id
        JOIN teams ht ON ht.team_id = f.home_team_id
        JOIN teams at ON at.team_id = f.away_team_id
        WHERE p.closing_odds IS NULL
          AND f.kickoff_time <= now() + interval '%s minutes'
          AND f.kickoff_time > now() - interval '15 minutes'
        """,
        (window_minutes,),
    )
    return cur.fetchall()


def update_closing_odds(cur, pick_id: int, closing_odds: float, clv_pct: float):
    cur.execute(
        """
        UPDATE picks
        SET closing_odds = %s, closing_captured_at = now(), clv_pct = %s
        WHERE pick_id = %s
        """,
        (closing_odds, clv_pct, pick_id),
    )


def run(bookmaker_key: str = "pinnacle"):
    print(f"[{datetime.now()}] Checking for picks needing closing line capture...")

    with get_cursor(commit=True) as cur:
        pending = get_picks_needing_closing_line(cur, CLOSING_LINE_WINDOW_MINUTES)

        if not pending:
            print("No picks currently in the closing-line capture window.")
            return

        print(f"Found {len(pending)} pick(s) needing closing odds.")

        # One fresh odds fetch covers all pending picks (the API returns
        # all upcoming fixtures, not just ones we care about)
        raw_events = fetch_odds_from_api()
        fixtures = parse_api_response(raw_events, bookmaker_key)
        odds_by_external_id = {f["external_id"]: f for f in fixtures}

        for pick in pending:
            fixture_odds = odds_by_external_id.get(pick["external_id"])
            if fixture_odds is None:
                print(f"  Warning: no current odds found for {pick['home_team']} vs "
                      f"{pick['away_team']} (fixture may have been removed/postponed)")
                continue

            outcome_key = {"home": "home_odds", "draw": "draw_odds", "away": "away_odds"}[pick["outcome"]]
            closing_odds = fixture_odds[outcome_key]

            clv_pct = compute_clv(float(pick["your_odds"]), float(closing_odds))

            update_closing_odds(cur, pick["pick_id"], closing_odds, clv_pct)

            print(f"  {pick['home_team']} vs {pick['away_team']} ({pick['outcome']}): "
                  f"odds {pick['your_odds']} -> {closing_odds}, CLV {clv_pct:+.2f}pp")

    print(f"[{datetime.now()}] Closing line capture complete.")


if __name__ == "__main__":
    run()
