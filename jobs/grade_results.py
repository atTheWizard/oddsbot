"""
jobs/grade_results.py

Scheduled job (run periodically, e.g. every few hours - matches finish
at different times so there's no single "right" moment). Two stages:

1. Updates fixtures.status to 'finished' and fills in home_goals/away_goals
   for any fixture whose kickoff_time has passed, by fetching results
   from the odds API's scores endpoint.
2. For every pick attached to a now-finished fixture, determines won/lost
   by comparing the actual result to the picked outcome, and computes
   profit_loss if a stake was recorded.

Run manually with: python -m jobs.grade_results
"""

import requests
from datetime import datetime

from config import ODDS_API_KEY, ODDS_API_BASE_URL, ODDS_API_SPORTS
from db.connection import get_cursor


def fetch_results_from_api() -> list[dict]:
    """
    Calls the odds API's scores endpoint for completed matches.
    Adjust parsing here if using a different provider than the-odds-api.com.
    """
    url = f"{ODDS_API_BASE_URL}/sports/{ODDS_API_SPORT}/scores"
    params = {"apiKey": ODDS_API_KEY, "daysFrom": 3}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_unfinished_fixtures_past_kickoff(cur) -> list[dict]:
    cur.execute(
        """
        SELECT fixture_id, external_id, home_team_id, away_team_id
        FROM fixtures
        WHERE status = 'scheduled' AND kickoff_time < now() - interval '2 hours'
        """
    )
    return cur.fetchall()


def update_fixture_result(cur, fixture_id: int, home_goals: int, away_goals: int):
    cur.execute(
        """
        UPDATE fixtures
        SET status = 'finished', home_goals = %s, away_goals = %s
        WHERE fixture_id = %s
        """,
        (home_goals, away_goals, fixture_id),
    )


def determine_actual_outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    elif home_goals < away_goals:
        return "away"
    return "draw"


def get_ungraded_picks_for_finished_fixtures(cur) -> list[dict]:
    cur.execute(
        """
        SELECT p.pick_id, p.outcome, p.your_odds, p.stake, f.home_goals, f.away_goals
        FROM picks p
        JOIN fixtures f ON f.fixture_id = p.fixture_id
        WHERE f.status = 'finished' AND p.result IS NULL
        """
    )
    return cur.fetchall()


def grade_pick(cur, pick: dict):
    actual_outcome = determine_actual_outcome(pick["home_goals"], pick["away_goals"])
    won = (actual_outcome == pick["outcome"])
    result = "won" if won else "lost"

    profit_loss = None
    if pick["stake"] is not None:
        stake = float(pick["stake"])
        if won:
            profit_loss = stake * (float(pick["your_odds"]) - 1)  # net profit
        else:
            profit_loss = -stake

    cur.execute(
        """
        UPDATE picks
        SET result = %s, profit_loss = %s
        WHERE pick_id = %s
        """,
        (result, profit_loss, pick["pick_id"]),
    )


def run():
    print(f"[{datetime.now()}] Starting results grading...")

    with get_cursor(commit=True) as cur:
        unfinished = get_unfinished_fixtures_past_kickoff(cur)

        if unfinished:
            print(f"Checking results for {len(unfinished)} fixtures past kickoff...")
            raw_results = fetch_results_from_api()
            results_by_external_id = {r["id"]: r for r in raw_results if r.get("completed")}

            for fixture in unfinished:
                result = results_by_external_id.get(fixture["external_id"])
                if result is None:
                    continue  # not finished yet according to the API, check again next run

                scores = {s["name"]: int(s["score"]) for s in result.get("scores", [])}
                # NOTE: matching team names from the scores payload to home/away
                # requires the same team name strings used during ingestion -
                # if your API's scores endpoint returns slightly different
                # naming, you may need a team-name normalization step here.
                home_goals = scores.get(result["home_team"])
                away_goals = scores.get(result["away_team"])

                if home_goals is None or away_goals is None:
                    continue

                update_fixture_result(cur, fixture["fixture_id"], home_goals, away_goals)

        ungraded_picks = get_ungraded_picks_for_finished_fixtures(cur)
        print(f"Grading {len(ungraded_picks)} pick(s) against finished fixtures...")

        for pick in ungraded_picks:
            grade_pick(cur, pick)

    print(f"[{datetime.now()}] Grading complete.")


if __name__ == "__main__":
    run()
