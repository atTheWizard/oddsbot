"""
jobs/run_scoring.py

Scheduled job (run once daily). For every upcoming fixture kicking off
within the next 3 days: computes team strengths from match history, runs
the Poisson model, and writes home/draw/away probabilities into
model_predictions.

Two guards against bad picks:
1. Only scores fixtures within 3 days - ignores far-future fixtures that
   have no business being picked today.
2. Skips any fixture where either team has fewer than MIN_MATCH_SAMPLE_SIZE
   finished matches in the database - neutral 1.0/1.0 strength ratings
   produce fake EV and should never reach the picks selector.

Run manually with: python -m jobs.run_scoring
"""

from datetime import datetime, timezone, timedelta

from config import RECENCY_DECAY, MIN_MATCH_SAMPLE_SIZE
from db.connection import get_cursor
from model.team_strength import compute_team_strength, MatchRecord
from model.poisson_model import predict_match


def get_league_averages(cur, league: str) -> tuple[float, float]:
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
    Pulls a team's most recent finished matches BEFORE before_kickoff.
    Strict 'before_kickoff' + 'finished' filters prevent look-ahead bias.
    """
    cur.execute(
        """
        SELECT
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

    return [
        MatchRecord(
            goals_for=row["goals_for"],
            goals_against=row["goals_against"],
            was_home=row["was_home"],
            games_ago=i,
        )
        for i, row in enumerate(rows)
    ]


def get_upcoming_fixtures(cur, days_ahead: int = 3) -> list[dict]:
    """
    Only returns fixtures kicking off within the next `days_ahead` days.
    This prevents the model from scoring August fixtures in July with
    neutral team strengths and producing fake high-EV picks.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    cur.execute(
        """
        SELECT fixture_id, home_team_id, away_team_id, league, kickoff_time
        FROM fixtures
        WHERE status = 'scheduled'
          AND kickoff_time > %(now)s
          AND kickoff_time <= %(cutoff)s
        """,
        {"now": now, "cutoff": cutoff},
    )
    return cur.fetchall()


def insert_prediction(cur, fixture_id: int, outcome: str, probability: float,
                      expected_goals_home: float, expected_goals_away: float):
    cur.execute(
        """
        INSERT INTO model_predictions
            (fixture_id, outcome, model_probability, expected_goals_home, expected_goals_away)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (fixture_id, outcome, float(probability), float(expected_goals_home), float(expected_goals_away)),
    )


def run():
    print(f"[{datetime.now()}] Starting scoring job (fixtures within next 3 days only)...")

    with get_cursor(commit=True) as cur:
        fixtures = get_upcoming_fixtures(cur, days_ahead=3)
        print(f"Found {len(fixtures)} fixtures within the next 3 days.")

        if not fixtures:
            print("No fixtures to score today. This is normal during off-season or mid-week.")
            return

        league_avg_cache: dict[str, tuple[float, float]] = {}
        scored = 0
        skipped_no_history = 0

        for fixture in fixtures:
            league = fixture["league"]
            if league not in league_avg_cache:
                league_avg_cache[league] = get_league_averages(cur, league)
            league_avg_home_goals, league_avg_away_goals = league_avg_cache[league]

            home_history = get_team_match_history(
                cur, fixture["home_team_id"], fixture["kickoff_time"]
            )
            away_history = get_team_match_history(
                cur, fixture["away_team_id"], fixture["kickoff_time"]
            )

            # GUARD: skip fixture if either team has insufficient history.
            # Neutral 1.0/1.0 strength ratings produce fake EV - these
            # fixtures should never reach the picks selector.
            if len(home_history) < MIN_MATCH_SAMPLE_SIZE or len(away_history) < MIN_MATCH_SAMPLE_SIZE:
                skipped_no_history += 1
                continue

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

            insert_prediction(cur, fixture["fixture_id"], "home",
                              prediction.prob_home_win, prediction.expected_goals_home,
                              prediction.expected_goals_away)
            insert_prediction(cur, fixture["fixture_id"], "draw",
                              prediction.prob_draw, prediction.expected_goals_home,
                              prediction.expected_goals_away)
            insert_prediction(cur, fixture["fixture_id"], "away",
                              prediction.prob_away_win, prediction.expected_goals_home,
                              prediction.expected_goals_away)
            scored += 1

        print(f"Scored: {scored} fixtures")
        if skipped_no_history:
            print(f"Skipped: {skipped_no_history} fixtures (insufficient team history)")

    print(f"[{datetime.now()}] Scoring complete.")


if __name__ == "__main__":
    run()
