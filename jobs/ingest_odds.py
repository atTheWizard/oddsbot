"""
jobs/ingest_odds.py

Scheduled job (run every few hours via cron/GitHub Actions). Loops over
all leagues in config.ODDS_API_SPORTS, fetches upcoming fixtures and
current odds from the odds API for each, upserts fixtures into the
database, and inserts a fresh snapshot row for every outcome into
odds_snapshots.

Design note: odds_snapshots is APPEND-ONLY. We never update an existing
snapshot row - every fetch inserts new rows. This builds the market-signal
history that ev_scoring.compute_market_signal_multiplier() depends on,
and lets us reconstruct "what were the odds at any point in time" for
debugging or backtesting.

Run manually with: python -m jobs.ingest_odds
"""

import requests
from datetime import datetime

from config import ODDS_API_KEY, ODDS_API_BASE_URL, ODDS_API_SPORTS
from db.connection import get_cursor


def fetch_odds_from_api(sport_key: str) -> list[dict]:
    """
    Calls the odds API for one sport/league key and returns raw JSON.
    Adjust parse_api_response() below if you use a different provider,
    but keep this function narrow: just the HTTP call.
    """
    url = f"{ODDS_API_BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",        # eu region covers the bookmakers relevant for these leagues
        "markets": "h2h",       # h2h = home/draw/away, matches our model's three outcomes
        "oddsFormat": "decimal",
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_api_response(raw_events: list[dict], bookmaker_key: str) -> list[dict]:
    """
    Parses the-odds-api.com response shape into a flat list of fixture dicts.
    One specific bookmaker's odds are extracted (we bet with one bookmaker,
    per the system design decision - no line-shopping across bookmakers).
    """
    parsed = []

    for event in raw_events:
        bookmaker_data = next(
            (b for b in event.get("bookmakers", []) if b.get("key") == bookmaker_key),
            None,
        )
        if bookmaker_data is None:
            continue  # chosen bookmaker doesn't have odds for this match yet

        h2h_market = next(
            (m for m in bookmaker_data.get("markets", []) if m.get("key") == "h2h"),
            None,
        )
        if h2h_market is None:
            continue

        outcomes = {o["name"]: o["price"] for o in h2h_market.get("outcomes", [])}
        home_team = event["home_team"]
        away_team = event["away_team"]

        home_odds = outcomes.get(home_team)
        away_odds = outcomes.get(away_team)
        draw_odds = outcomes.get("Draw")

        if home_odds is None or away_odds is None or draw_odds is None:
            continue  # incomplete market, skip rather than insert partial data

        parsed.append({
            "external_id": event["id"],
            "home_team": home_team,
            "away_team": away_team,
            "league": event.get("sport_key", "unknown"),
            "kickoff_time": event["commence_time"],
            "home_odds": home_odds,
            "draw_odds": draw_odds,
            "away_odds": away_odds,
        })

    return parsed


def upsert_team(cur, name: str, league: str) -> int:
    """Inserts a team if it doesn't exist, returns its team_id either way."""
    cur.execute(
        """
        INSERT INTO teams (name, league)
        VALUES (%s, %s)
        ON CONFLICT (name) DO UPDATE SET league = EXCLUDED.league
        RETURNING team_id
        """,
        (name, league),
    )
    return cur.fetchone()["team_id"]


def upsert_fixture(cur, fixture: dict, home_team_id: int, away_team_id: int) -> int:
    """Inserts a fixture if it doesn't exist (keyed by external_id from the API),
    updates kickoff_time if it changed (postponement/reschedule), and returns
    the fixture_id. Never overwrites home_goals/away_goals/status - those are
    owned by the grading job."""
    cur.execute(
        """
        INSERT INTO fixtures (home_team_id, away_team_id, league, kickoff_time, external_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET kickoff_time = EXCLUDED.kickoff_time
        RETURNING fixture_id
        """,
        (home_team_id, away_team_id, fixture["league"],
         fixture["kickoff_time"], fixture["external_id"]),
    )
    return cur.fetchone()["fixture_id"]


def insert_odds_snapshot(cur, fixture_id: int, outcome: str, odds: float):
    """Always inserts a new row - never updates. See module docstring."""
    cur.execute(
        """
        INSERT INTO odds_snapshots (fixture_id, outcome, odds)
        VALUES (%s, %s, %s)
        """,
        (fixture_id, outcome, odds),
    )


def run(bookmaker_key: str = "pinnacle"):
    """
    Main entry point. Loops over all leagues in ODDS_API_SPORTS.

    bookmaker_key: set to the key string the-odds-api.com uses for the
    bookmaker you actually bet with. 'pinnacle' is the default since it's
    the sharpest reference book in the EU region - swap it for your actual
    bookmaker (e.g. 'bet365', 'williamhill', 'unibet') once you know which
    key string they use. Check the-odds-api.com's /sports/{sport}/odds
    response for the 'key' field inside the 'bookmakers' array to confirm.
    """
    print(f"[{datetime.now()}] Starting odds ingestion for {len(ODDS_API_SPORTS)} leagues...")

    total_fixtures = 0

    with get_cursor(commit=True) as cur:
        for sport_key in ODDS_API_SPORTS:
            print(f"  Fetching: {sport_key}")
            try:
                raw_events = fetch_odds_from_api(sport_key)
                fixtures = parse_api_response(raw_events, bookmaker_key)
                print(f"    {len(raw_events)} events -> {len(fixtures)} with usable odds")

                for fixture in fixtures:
                    home_team_id = upsert_team(cur, fixture["home_team"], fixture["league"])
                    away_team_id = upsert_team(cur, fixture["away_team"], fixture["league"])
                    fixture_id = upsert_fixture(cur, fixture, home_team_id, away_team_id)

                    insert_odds_snapshot(cur, fixture_id, "home", fixture["home_odds"])
                    insert_odds_snapshot(cur, fixture_id, "draw", fixture["draw_odds"])
                    insert_odds_snapshot(cur, fixture_id, "away", fixture["away_odds"])

                total_fixtures += len(fixtures)

            except requests.HTTPError as e:
                # Log the error but keep going - a single league failing
                # (e.g. off-season, API rate limit) shouldn't abort all others
                print(f"    WARNING: HTTP error for {sport_key}: {e}")
            except Exception as e:
                print(f"    WARNING: Unexpected error for {sport_key}: {e}")

    print(f"[{datetime.now()}] Ingestion complete. {total_fixtures} fixtures across all leagues.")


if __name__ == "__main__":
    run()