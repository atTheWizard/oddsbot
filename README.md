# value_bet_bot

A daily value-bet picks system: pulls soccer odds from an API, scores
every fixture with a Poisson goals model, compares the model's
probability against bookmaker odds to find positive expected value (EV),
and sends the top 2-5 picks to Telegram. Tracks closing-line value (CLV)
and grades results so you can honestly tell whether the model has real
edge over time.

This is a research/tracking tool, not a guaranteed profit system - see
the model/ docstrings for the honest limitations of the underlying math.

## Project structure

```
value_bet_bot/
├── config.py              # loads settings from environment variables
├── .env.example           # copy to .env and fill in real values
├── requirements.txt
├── Dockerfile             # packages the app for Railway/VPS deployment
├── .dockerignore
├── docker-compose.yml      # local Docker testing only
├── run_daily.py            # chains ingest -> score -> pick -> Telegram into one command
├── closing_lines_loop.py   # continuous polling loop alternative to per-cron-entry scheduling
├── db/
│   ├── schema.sql          # run once to set up Postgres tables
│   └── connection.py       # shared DB connection helper
├── model/                  # pure logic, no I/O - already written & tested
│   ├── team_strength.py     # recency-weighted attack/defense ratings, H2H, injury discount
│   ├── poisson_model.py     # expected goals -> scoreline grid -> win/draw/loss probabilities
│   ├── ev_scoring.py        # odds -> implied probability, EV, market signal, CLV
│   └── picks_selector.py    # filters/ranks/dedupes down to top 2-5 daily picks
├── jobs/                   # scheduled scripts, the only things that touch DB/API
│   ├── ingest_odds.py        # every few hours: pulls fixtures + odds
│   ├── run_scoring.py        # daily: runs the Poisson model on upcoming fixtures
│   ├── select_picks.py       # daily: EV filter + ranking -> writes picks table
│   ├── capture_closing_lines.py  # every 10-15 min: captures closing odds, computes CLV
│   └── grade_results.py      # periodic: marks picks won/lost, computes P/L
├── bot/
│   └── telegram_bot.py      # sends daily digest, handles /stake replies
├── backtest/
│   └── run_backtest.py      # chronological historical simulation (look-ahead-bias safe)
└── tests/
    └── test_pipeline.py     # end-to-end smoke test with made-up fixtures
```

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt --break-system-packages
   ```

2. **Set up Postgres** - create a free database on Neon or Supabase, then:
   ```bash
   psql $DATABASE_URL -f db/schema.sql
   ```

3. **Get an odds API key** - sign up at the-odds-api.com (free tier available).

4. **Create your Telegram bot** - message @BotFather on Telegram, run
   `/newbot`, copy the token. Message your new bot once, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat_id.

5. **Configure environment**
   ```bash
   cp .env.example .env
   # edit .env with your real DATABASE_URL, ODDS_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
   ```

## Running it locally (manual, for testing)

Run the smoke test first to confirm the core logic works:
```bash
python -m tests.test_pipeline
```

Daily pipeline, chained into one command:
```bash
python run_daily.py
```

Closing-line capture (run frequently, separate schedule - e.g. every 10-15 min all day):
```bash
python -m jobs.capture_closing_lines
```

Results grading (run periodically, e.g. every few hours):
```bash
python -m jobs.grade_results
```

## Running it with Docker locally

```bash
docker compose build
docker compose run --rm daily          # runs the full daily pipeline once
docker compose up closing-lines         # runs the closing-line loop continuously
```
Requires `.env` filled in (see Setup above) - `docker-compose.yml` loads it automatically.

## Deploying to Railway (recommended - this is what makes it actually automatic)

Docker by itself doesn't make anything run on a schedule - it just packages
the app so it runs identically anywhere. Something still needs to host the
container and trigger it. Railway does both: it builds your `Dockerfile`
automatically and has a built-in cron schedule field, so there's no server
for you to maintain.

1. Push this project to a GitHub repo (private is fine, `.env` is gitignored
   so your secrets never get committed).
2. On railway.app, create a new project -> "Deploy from GitHub repo" -> select it.
   Railway detects the `Dockerfile` and builds it automatically.
3. Add your environment variables in Railway's dashboard (Variables tab):
   `DATABASE_URL`, `ODDS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   and any of the tuning variables from `.env.example` you want to override.
4. Create the scheduled jobs (Railway calls these "Cron Jobs" - add one per
   job, each pointing at this same repo/image):

   | Job | Command | Schedule (cron syntax) |
   |---|---|---|
   | Daily pipeline | `python run_daily.py` | `0 6 * * *` (6am daily, adjust to your timezone) |
   | Closing lines | `python -m jobs.capture_closing_lines` | `*/10 * * * *` (every 10 min) |
   | Grade results | `python -m jobs.grade_results` | `0 */4 * * *` (every 4 hours) |

5. Each cron job spins up a fresh container, runs the command, then shuts
   down - you're not paying for an idle always-on server, only for the
   seconds each job actually runs (Railway's free tier covers this kind of
   usage comfortably for one project).

This is the "set it up once, forget about it" version - once deployed,
picks arrive in Telegram daily with no further action from you, other
than placing the bet and replying `/stake` after each one.

## Alternative: VPS + Docker + cron

If you'd rather own the server outright (more control, similar cost):
1. Get a small VPS (Hetzner/DigitalOcean, ~$4-6/month).
2. `git clone` your repo onto it, fill in `.env`, run `docker compose build`.
3. Add cron entries (`crontab -e`) that call `docker compose run --rm daily`
   on the schedule from the table above, and run `closing_lines_loop.py`
   as a persistent background service (e.g. via `docker compose up -d closing-lines`,
   which restarts automatically per `restart: unless-stopped`).

Recording a stake after you place a bet - either reply `/stake <pick_id> <amount>`
to the bot (requires `python -m bot.telegram_bot listen` running), or insert
directly into the `picks` table.

## Backtesting

Before trusting any of this with real stakes, wire up `load_historical_data()`
in `backtest/run_backtest.py` to a real historical results+odds source, then:
```bash
python -m backtest.run_backtest
```
Look at ROI and average CLV over hundreds of picks, ideally across multiple
seasons, before drawing any conclusions about whether the model has edge.

## Scheduling

Any of these work for free/low-cost scheduling:
- Cron on a small VPS
- GitHub Actions `schedule:` trigger (free for low usage)
- Railway/Render cron jobs

The closing-line job is the one exception that needs frequent polling
rather than a single daily run - see its docstring for why.
# oddsbot
