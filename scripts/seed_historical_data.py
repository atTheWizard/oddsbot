"""
scripts/seed_historical_data.py

Downloads last season's match results from football-data.co.uk (free,
no API key needed) and loads them into the fixtures, teams, and
odds_snapshots tables so the Poisson model has real team strength
history from day one instead of neutral 1.0/1.0 ratings.

Only loads the 9 leagues you selected. Skips any league not available
on football-data.co.uk (Champions League, Europa League - those use
a different data source since they're knockout tournaments).

Run once before deploying:
    docker compose run --rm daily python -m scripts.seed_historical_data

Or locally:
    python -m scripts.seed_historical_data

Data source: football-data.co.uk - free, reliable, updated daily.
Seasons available: going back to the 1990s for major leagues.
"""

import requests
import csv
import io
from datetime import datetime, timezone

from db.connection import get_cursor

# --- League mapping ---
# football-data.co.uk uses specific path codes per league per season.
# Format: base_url/SEASON/CODE.csv
# SEASON: 2425 = 2024/25, 2324 = 2023/24, etc.
# We load TWO seasons to give the model enough history for all teams.

BASE_URL = "https://www.football-data.co.uk/mmz4281"

LEAGUE_MAP = {
    # odds_api_key: (fd_code, display_name)
    "soccer_epl":                    ("E0", "EPL"),
    "soccer_spain_la_liga":          ("SP1", "La Liga"),
    "soccer_italy_serie_a":          ("I1", "Serie A"),
    "soccer_germany_bundesliga":     ("D1", "Bundesliga"),
    "soccer_france_ligue_one":       ("F1", "Ligue 1"),
    "soccer_portugal_primeira_liga": ("P1", "Primeira Liga"),
    "soccer_netherlands_eredivisie": ("N1", "Eredivisie"),
    # Champions League + Europa League not available on football-data.co.uk
    # They will build up naturally once live matches are graded
}

# Load these two seasons - gives ~760 matches per league
SEASONS = ["2425", "2324"]


def download_csv(fd_code: str, season: str) -> list[dict] | None:
    """
    Downloads one season CSV from football-data.co.uk.
    Returns list of row dicts, or None if the file doesn't exist
    (e.g. a league wasn't in that division that season).
    """
    url = f"{BASE_URL}/{season}/{fd_code}.csv"
    print(f"    Downloading: {url}")

    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            print(f"    Not found (league may not exist for this season) - skipping")
            return None
        response.raise_for_status()

        # football-data.co.uk CSVs sometimes have trailing empty rows/columns
        content = response.text.strip()
        reader = csv.DictReader(io.StringIO(content))
        rows = [row for row in reader if row.get("HomeTeam") and row.get("AwayTeam")]
        return rows

    except requests.RequestException as e:
        print(f"    Download error: {e}")
        return None


def parse_date(date_str: str) -> datetime | None:
    """
    football-data.co.uk uses DD/MM/YY or DD/MM/YYYY format.
    Returns a UTC datetime at 15:00 (approximate kickoff) or None if unparseable.
    """
    date_str = date_str.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(hour=15, minute=0, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def upsert_team(cur, name: str, league: str) -> int:
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


def insert_finished_fixture(
    cur,
    home_team_id: int,
    away_team_id: int,
    league: str,
    kickoff_time: datetime,
    home_goals: int,
    away_goals: int,
    external_id: str,
) -> int | None:
    """
    Inserts a finished historical match. Uses ON CONFLICT DO NOTHING so
    re-running the seeder is safe - it won't duplicate matches already loaded.
    Returns fixture_id if inserted, None if it already existed.
    """
    cur.execute(
        """
        INSERT INTO fixtures
            (home_team_id, away_team_id, league, kickoff_time,
             status, home_goals, away_goals, external_id)
        VALUES (%s, %s, %s, %s, 'finished', %s, %s, %s)
        ON CONFLICT (external_id) DO NOTHING
        RETURNING fixture_id
        """,
        (home_team_id, away_team_id, league, kickoff_time,
         home_goals, away_goals, external_id),
    )
    row = cur.fetchone()
    return row["fixture_id"] if row else None


def insert_odds_snapshot(cur, fixture_id: int, outcome: str, odds: float):
    cur.execute(
        """
        INSERT INTO odds_snapshots (fixture_id, outcome, odds)
        VALUES (%s, %s, %s)
        """,
        (fixture_id, outcome, odds),
    )


def seed_league(cur, odds_api_key: str, fd_code: str, display_name: str):
    """Seeds all available seasons for one league."""
    print(f"\n  {display_name} ({fd_code})")
    total_inserted = 0

    for season in SEASONS:
        rows = download_csv(fd_code, season)
        if not rows:
            continue

        inserted = 0
        skipped = 0

        for row in rows:
            home_team = row.get("HomeTeam", "").strip()
            away_team = row.get("AwayTeam", "").strip()
            date_str = row.get("Date", "").strip()

            # Final score columns: FTHG = Full Time Home Goals, FTAG = Full Time Away Goals
            fthg = row.get("FTHG", "").strip()
            ftag = row.get("FTAG", "").strip()

            if not all([home_team, away_team, date_str, fthg, ftag]):
                skipped += 1
                continue

            try:
                home_goals = int(float(fthg))
                away_goals = int(float(ftag))
            except ValueError:
                skipped += 1
                continue

            kickoff_time = parse_date(date_str)
            if kickoff_time is None:
                skipped += 1
                continue

            # Stable external ID for deduplication
            external_id = f"hist_{fd_code}_{season}_{home_team}_{away_team}_{date_str}".replace(" ", "_")

            home_team_id = upsert_team(cur, home_team, odds_api_key)
            away_team_id = upsert_team(cur, away_team, odds_api_key)

            fixture_id = insert_finished_fixture(
                cur, home_team_id, away_team_id, odds_api_key,
                kickoff_time, home_goals, away_goals, external_id
            )

            if fixture_id is None:
                skipped += 1  # already existed
                continue

            # Also seed pre-match odds if available in the CSV
            # football-data.co.uk includes Pinnacle odds in columns:
            # PSH (Pinnacle home), PSD (Pinnacle draw), PSA (Pinnacle away)
            psh = row.get("PSH", "").strip()
            psd = row.get("PSD", "").strip()
            psa = row.get("PSA", "").strip()

            if psh and psd and psa:
                try:
                    insert_odds_snapshot(cur, fixture_id, "home", float(psh))
                    insert_odds_snapshot(cur, fixture_id, "draw", float(psd))
                    insert_odds_snapshot(cur, fixture_id, "away", float(psa))
                except ValueError:
                    pass  # odds missing/malformed for this row, not critical

            inserted += 1

        print(f"    Season {season}: {inserted} matches inserted, {skipped} skipped")
        total_inserted += inserted

    return total_inserted


def run():
    print(f"[{datetime.now()}] Starting historical data seed...")
    print(f"Loading {len(LEAGUE_MAP)} leagues, {len(SEASONS)} seasons each")
    print("Note: Champions League + Europa League not available on football-data.co.uk")
    print("      They will build up naturally as live matches are graded.\n")

    grand_total = 0

    with get_cursor(commit=True) as cur:
        for odds_api_key, (fd_code, display_name) in LEAGUE_MAP.items():
            total = seed_league(cur, odds_api_key, fd_code, display_name)
            grand_total += total

    print(f"\n[{datetime.now()}] Seed complete.")
    print(f"Total matches loaded: {grand_total}")
    print("\nNext steps:")
    print("  1. Run: docker compose run --rm daily python -m jobs.run_scoring")
    print("  2. Run: docker compose run --rm daily python -m jobs.select_picks")
    print("  3. Run: docker compose run --rm daily python -m bot.telegram_bot send")
    print("  Picks should now reflect real team strengths.")


if __name__ == "__main__":
    run()
