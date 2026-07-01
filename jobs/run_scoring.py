"""
jobs/run_scoring.py

Scheduled job (run once daily). For every upcoming, not-yet-finished
fixture: computes team strengths from match history, runs the Poisson
model, and writes the resulting home/draw/away probabilities into
model_predictions.

IMPORTANT (look-ahead bias): get_team_match_history() below only pulls
fixtures with kickoff_time BEFORE the fixture being predicted, and only
ones that are already 'finished' with goals recorded. This is the
guardrail discussed in the system design that protects any future
backtest from accidentally letting the model see the future.

Run manually with: python -m jobs.run_scoring
"""

from datetime import datetime

from config import RECENCY_DECAY, MIN_MATCH_SAMPLE_SIZE
from db.connection import get_cursor
from model.team_strength import compute_team_strength, MatchRecord
from model.poisson_model import predict_match
from model.team_strength import TeamStrength


def get_league_averages(cur, league: str) -> tuple[float, float]:
    """
    Computes league-wide average home/away goals from finished matches
    in this league. Falls back to generic soccer defaults (1.5/1.2) if
    there isn't enough finished-match data yet for this league - this
    matters early on, before you've accumulated history.
    """
    cur.execute(
        """
        SELECT AVG(home_goals) as avg_home, AVG(away_goals) as avg_away
        FROM fixtures
        WHERE league = %s AND status = 'finished'
        """,
        (league,),
    )
    row = cur.fetchone()
    avg_home = float(row["avg_home"]) if row["avg_home"] is not None else 1.5
    avg_away = float(row["avg_away"]) if row["avg_away"] is not None else 1.2
    return avg_home, avg_away


def get_team_match_history(cur, team_id: int, before_kickoff: datetime, limit: int = 10) -> list[MatchRecord]:
    """
    Pulls a team's most recent finished matches BEFORE before_kickoff,
    as both home and away, and converts them into MatchRecord objects
    with games_ago computed relative to recency.

    The strict "before_kickoff" filter and "status = finished" filter
    together are what prevent look-ahead bias - see module docstring.
    """
    cur.execute(
        """
        SELECT
            kickoff_time,
            CASE WHEN home_team_id = %(team_id)s THEN home_goals ELSE away_goals END as goals_for,
            CASE WHEN home_team_id = %(team_id)s THEN away_goals ELSE home_goals END as goals_against,
            (home_team_id = %(team_id)s) as was_home
        FROM fixtures
        WHERE (home_team_id = %(team_id)s OR away_team_id = %(team_id)s)
          AND status = 'finished'
          AND kickoff_time < %(before_kickoff)s
        ORDER BY kickoff_time DESC
        LIMIT %(limit)s
        """,
        {"team_id": team_id, "before_kickoff": before_kickoff, "limit": limit},
    )
    rows = cur.fetchall()

    history = []
    for games_ago, row in enumerate(rows):
        history.append(MatchRecord(
            goals_for=row["goals_for"],
            goals_against=row["goals_against"],
            was_home=row["was_home"],
            games_ago=games_ago,
        ))
    return history


def get_upcoming_fixtures(cur) -> list[dict]:
    """Fixtures that haven't kicked off yet and haven't been scored yet today."""
    cur.execute(
        """
        SELECT fixture_id, home_team_id, away_team_id, league, kickoff_time
        FROM fixtures
        WHERE status = 'scheduled' AND kickoff_time > now()
        """
    )
    return cur.fetchall()


def insert_prediction(cur, fixture_id: int, outcome: str, probability: float,
                       expected_goals_home: float, expected_goals_away: float):
    cur.execute(
        """
        INSERT INTO model_predictions
            (fixture_id, outcome, model_probability, expected_goals_home, expected_goals_away)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (fixture_id, outcome, probability, expected_goals_home, expected_goals_away),
    )


def run():
    print(f"[{datetime.now()}] Starting scoring job...")

    with get_cursor(commit=True) as cur:
        fixtures = get_upcoming_fixtures(cur)
        print(f"Found {len(fixtures)} upcoming fixtures to score.")

        league_avg_cache: dict[str, tuple[float, float]] = {}

        for fixture in fixtures:
            league = fixture["league"]
            if league not in league_avg_cache:
                league_avg_cache[league] = get_league_averages(cur, league)
            league_avg_home_goals, league_avg_away_goals = league_avg_cache[league]

            home_history = get_team_match_history(cur, fixture["home_team_id"], fixture["kickoff_time"])
            away_history = get_team_match_history(cur, fixture["away_team_id"], fixture["kickoff_time"])

            home_strength = compute_team_strength(
                home_history, league_avg_home_goals, league_avg_away_goals,
                decay=RECENCY_DECAY, min_sample_size=MIN_MATCH_SAMPLE_SIZE,
            )
            away_strength = compute_team_strength(
                away_history, league_avg_home_goals, league_avg_away_goals,
                decay=RECENCY_DECAY, min_sample_size=MIN_MATCH_SAMPLE_SIZE,
            )

            prediction = predict_match(
                home_team=home_strength,
                away_team=away_strength,
                league_avg_home_goals=league_avg_home_goals,
                league_avg_away_goals=league_avg_away_goals,
            )

            insert_prediction(cur, fixture["fixture_id"], "home", prediction.prob_home_win,
                               prediction.expected_goals_home, prediction.expected_goals_away)
            insert_prediction(cur, fixture["fixture_id"], "draw", prediction.prob_draw,
                               prediction.expected_goals_home, prediction.expected_goals_away)
            insert_prediction(cur, fixture["fixture_id"], "away", prediction.prob_away_win,
                               prediction.expected_goals_home, prediction.expected_goals_away)

    print(f"[{datetime.now()}] Scoring complete.")


if __name__ == "__main__":
    run()
