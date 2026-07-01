"""
config.py

Central place all jobs/bot/db code imports settings from. Reads from
environment variables (loaded from .env locally via python-dotenv, or set
directly as real environment variables on a hosting platform).

Never hardcode secrets in any other file - they all come through here.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    """Fetches a required env var, raising a clear error if it's missing
    rather than silently proceeding with None and failing somewhere
    confusing later."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {key}. "
            f"Check your .env file or hosting platform's environment settings."
        )
    return value


# --- Database ---
DATABASE_URL = _require("DATABASE_URL")  # e.g. postgres://user:pass@host/dbname

# --- Odds API ---
ODDS_API_KEY = _require("ODDS_API_KEY")
ODDS_API_BASE_URL = os.environ.get("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")

# All leagues to ingest. Sourced from the-odds-api.com's official sport key list.
# To add/remove leagues, set ODDS_API_SPORTS as a comma-separated list in .env,
# or modify the default list below. Keys confirmed from the-odds-api.com docs.
_sports_env = os.environ.get("ODDS_API_SPORTS", "")
ODDS_API_SPORTS: list[str] = (
    [s.strip() for s in _sports_env.split(",") if s.strip()]
    if _sports_env
    else [
        "soccer_epl",                    # English Premier League
        "soccer_spain_la_liga",          # La Liga - Spain
        "soccer_italy_serie_a",          # Serie A - Italy
        "soccer_germany_bundesliga",     # Bundesliga - Germany
        "soccer_france_ligue_one",       # Ligue 1 - France
        "soccer_uefa_champs_league",     # UEFA Champions League
        "soccer_uefa_europa_league",     # UEFA Europa League
        "soccer_portugal_primeira_liga", # Primeira Liga - Portugal
        "soccer_netherlands_eredivisie", # Dutch Eredivisie - Netherlands
    ]
)

# --- Telegram ---
TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _require("TELEGRAM_CHAT_ID")

# --- Model / picks tuning ---
MIN_EV_THRESHOLD_PCT = float(os.environ.get("MIN_EV_THRESHOLD_PCT", "3.0"))
MAX_DAILY_PICKS = int(os.environ.get("MAX_DAILY_PICKS", "5"))
RECENCY_DECAY = float(os.environ.get("RECENCY_DECAY", "0.9"))
MIN_MATCH_SAMPLE_SIZE = int(os.environ.get("MIN_MATCH_SAMPLE_SIZE", "5"))

# --- Closing line capture timing ---
CLOSING_LINE_WINDOW_MINUTES = int(os.environ.get("CLOSING_LINE_WINDOW_MINUTES", "30"))

# --- Bookmaker ---
BOOKMAKER_KEY = os.environ.get("BOOKMAKER_KEY", "pinnacle")  # change to your bookmaker