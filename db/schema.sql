-- schema.sql
-- Run this once against your Postgres database (Neon, Supabase, etc) to set
-- up all tables. Matches the schema designed in the system design discussion.
--
-- Usage: psql $DATABASE_URL -f db/schema.sql

CREATE TABLE IF NOT EXISTS teams (
    team_id        SERIAL PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    league         TEXT NOT NULL,
    attack_strength   NUMERIC DEFAULT 1.0,
    defense_strength  NUMERIC DEFAULT 1.0,
    last_updated   TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id     SERIAL PRIMARY KEY,
    home_team_id   INTEGER REFERENCES teams(team_id),
    away_team_id   INTEGER REFERENCES teams(team_id),
    league         TEXT NOT NULL,
    kickoff_time   TIMESTAMP NOT NULL,
    status         TEXT DEFAULT 'scheduled',  -- scheduled / finished / postponed
    home_goals     INTEGER,
    away_goals     INTEGER,
    external_id    TEXT UNIQUE  -- the odds API's own fixture id, for upserts
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id    SERIAL PRIMARY KEY,
    fixture_id     INTEGER REFERENCES fixtures(fixture_id),
    outcome        TEXT NOT NULL,   -- 'home', 'draw', 'away'
    odds           NUMERIC NOT NULL,
    fetched_at     TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_predictions (
    prediction_id   SERIAL PRIMARY KEY,
    fixture_id      INTEGER REFERENCES fixtures(fixture_id),
    outcome         TEXT NOT NULL,
    model_probability NUMERIC NOT NULL,
    expected_goals_home NUMERIC,
    expected_goals_away NUMERIC,
    predicted_at    TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS picks (
    pick_id          SERIAL PRIMARY KEY,
    fixture_id       INTEGER REFERENCES fixtures(fixture_id),
    outcome          TEXT NOT NULL,
    model_probability NUMERIC NOT NULL,
    your_odds        NUMERIC NOT NULL,
    ev_pct           NUMERIC NOT NULL,
    flagged_at       TIMESTAMP DEFAULT now(),
    closing_odds     NUMERIC,
    closing_captured_at TIMESTAMP,
    clv_pct          NUMERIC,
    sent_to_telegram BOOLEAN DEFAULT false,
    stake            NUMERIC,
    result           TEXT,         -- 'won' / 'lost' / 'pending'
    profit_loss      NUMERIC
);

-- Useful indexes for the query patterns the jobs will actually run
CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff ON fixtures(kickoff_time);
CREATE INDEX IF NOT EXISTS idx_fixtures_status ON fixtures(status);
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_fixture ON odds_snapshots(fixture_id, outcome);
CREATE INDEX IF NOT EXISTS idx_picks_result ON picks(result);
