"""
jobs/select_picks.py

Scheduled job (run right after run_scoring, same daily window). Joins
the latest model_predictions against the most recent odds_snapshots for
each fixture, scores every outcome via model.ev_scoring, and uses
model.picks_selector to filter down to the final top 2-5 picks. Inserts
the result into the picks table.

Run manually with: python -m jobs.select_picks
"""

from datetime import datetime

from config import MIN_EV_THRESHOLD_PCT, MAX_DAILY_PICKS
from db.connection import get_cursor
from model.ev_scoring import score_outcome
from model.picks_selector import FixtureOutcomeScore, select_top_picks


def get_predictions_with_latest_odds(cur) -> list[dict]:
    """
    For every fixture with a prediction and no pick sent yet today, joins
    against the MOST RECENT odds_snapshot for that fixture+outcome
    (DISTINCT ON picks the latest row per fixture/outcome pair, ordered
    by fetched_at descending).
    """
    cur.execute(
        """
        SELECT DISTINCT ON (mp.fixture_id, mp.outcome)
            mp.fixture_id,
            mp.outcome,
            mp.model_probability,
            os.odds as current_odds,
            ht.name as home_team,
            at.name as away_team
        FROM model_predictions mp
        JOIN fixtures f ON f.fixture_id = mp.fixture_id
        JOIN teams ht ON ht.team_id = f.home_team_id
        JOIN teams at ON at.team_id = f.away_team_id
        JOIN odds_snapshots os
            ON os.fixture_id = mp.fixture_id AND os.outcome = mp.outcome
        WHERE f.status = 'scheduled'
          AND mp.fixture_id NOT IN (SELECT fixture_id FROM picks WHERE flagged_at::date = CURRENT_DATE)
        ORDER BY mp.fixture_id, mp.outcome, os.fetched_at DESC
        """
    )
    return cur.fetchall()


def insert_pick(cur, fixture_id: int, outcome: str, model_probability: float,
                 odds: float, ev_pct: float):
    cur.execute(
        """
        INSERT INTO picks (fixture_id, outcome, model_probability, your_odds, ev_pct)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (fixture_id, outcome, model_probability, odds, ev_pct),
    )


def run():
    print(f"[{datetime.now()}] Starting picks selection...")

    with get_cursor(commit=True) as cur:
        rows = get_predictions_with_latest_odds(cur)
        print(f"Scoring {len(rows)} prediction/odds pairs...")

        all_scores = []
        for row in rows:
            score = score_outcome(
                outcome=row["outcome"],
                model_probability=float(row["model_probability"]),
                current_odds=float(row["current_odds"]),
            )
            all_scores.append(FixtureOutcomeScore(
                fixture_id=row["fixture_id"],
                home_team=row["home_team"],
                away_team=row["away_team"],
                score=score,
            ))

        picks = select_top_picks(
            all_scores,
            min_ev_pct=MIN_EV_THRESHOLD_PCT,
            max_picks=MAX_DAILY_PICKS,
        )

        print(f"Selected {len(picks)} picks (threshold: EV >= {MIN_EV_THRESHOLD_PCT}%).")

        for pick in picks:
            insert_pick(
                cur,
                fixture_id=pick.fixture_id,
                outcome=pick.score.outcome,
                model_probability=pick.score.model_probability,
                odds=pick.score.odds,
                ev_pct=pick.score.ev_pct,
            )

    print(f"[{datetime.now()}] Picks selection complete.")
    return picks if rows else []


if __name__ == "__main__":
    run()
