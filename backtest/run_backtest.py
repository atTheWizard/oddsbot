"""
backtest/run_backtest.py

Simulates what the system would have picked over a historical period,
then grades those picks against what actually happened. Computes ROI
and average CLV (using closing odds from the historical data) - the two
honest pass/fail numbers discussed in the system design.

CRITICAL DESIGN RULE - look-ahead bias:
Matches are processed in strict chronological order. For each match, team
strength is computed using ONLY matches that happened before it (the
matches list must already be sorted ascending by date before calling
run_backtest). We never compute a season-long average and apply it
backwards - that would let the model "see the future" and produce a
fake, inflated backtest result.

This file expects historical data already loaded into Python objects
(see HistoricalMatch below) - wiring up a specific historical data
source (e.g. a CSV export, football-data.co.uk, a paid historical odds
API) is a separate step. This runner is provider-agnostic.

Run with: python -m backtest.run_backtest (after filling in load_historical_data())
"""

from dataclasses import dataclass
from collections import defaultdict

from model.team_strength import compute_team_strength, MatchRecord
from model.poisson_model import predict_match
from model.ev_scoring import score_outcome, compute_clv


@dataclass
class HistoricalMatch:
    """One real historical match, including the odds that were actually
    available before kickoff and the closing odds near kickoff."""
    match_id: str
    date: str  # ISO format, used only for display/sorting verification
    home_team: str
    away_team: str
    league: str
    home_goals: int
    away_goals: int
    pre_match_home_odds: float
    pre_match_draw_odds: float
    pre_match_away_odds: float
    closing_home_odds: float
    closing_draw_odds: float
    closing_away_odds: float


@dataclass
class BacktestPick:
    match: HistoricalMatch
    outcome: str
    model_probability: float
    odds_used: float
    ev_pct: float
    clv_pct: float
    won: bool
    stake: float
    profit_loss: float


def load_historical_data() -> list[HistoricalMatch]:
    """
    PLACEHOLDER - replace this with real loading logic for your chosen
    historical data source (CSV file, database export, API). Must return
    matches sorted ascending by date (oldest first).

    The dataclass above defines exactly what fields you need per match -
    final score, plus odds both pre-match and at closing.
    """
    raise NotImplementedError(
        "Wire this up to your historical data source. See module docstring."
    )


def run_backtest(
    matches: list[HistoricalMatch],
    min_ev_pct: float = 3.0,
    flat_stake: float = 10.0,
    league_avg_window: int = 10,
) -> list[BacktestPick]:
    """
    Processes matches in chronological order. For each match:
    1. Computes team strength using only prior matches in this league
       (the team_history dict accumulates as we go - this IS the
       look-ahead-bias guard).
    2. Runs the Poisson model to get probabilities.
    3. Scores all 3 outcomes against the PRE-MATCH odds (what would have
       actually been available to bet at the time).
    4. If any outcome clears min_ev_pct, records it as a pick, grades it
       against the actual result, and computes CLV using the closing odds.
    5. After grading, adds this match's result into team_history so future
       matches benefit from it - but never before this match was scored.
    """
    team_history: dict[str, list[MatchRecord]] = defaultdict(list)
    league_goals: dict[str, list[tuple[int, int]]] = defaultdict(list)  # (home_goals, away_goals) per league
    picks: list[BacktestPick] = []

    for match in matches:
        league = match.league

        # League averages computed from matches seen SO FAR ONLY
        past_league_matches = league_goals[league]
        if past_league_matches:
            avg_home = sum(h for h, a in past_league_matches) / len(past_league_matches)
            avg_away = sum(a for h, a in past_league_matches) / len(past_league_matches)
        else:
            avg_home, avg_away = 1.5, 1.2  # fallback before any history exists

        home_hist = team_history[match.home_team][-league_avg_window:]
        away_hist = team_history[match.away_team][-league_avg_window:]

        # games_ago must be recomputed relative to THIS match, most recent first
        home_hist_indexed = [
            MatchRecord(m.goals_for, m.goals_against, m.was_home, games_ago=i)
            for i, m in enumerate(reversed(home_hist))
        ]
        away_hist_indexed = [
            MatchRecord(m.goals_for, m.goals_against, m.was_home, games_ago=i)
            for i, m in enumerate(reversed(away_hist))
        ]

        home_strength = compute_team_strength(home_hist_indexed, avg_home, avg_away)
        away_strength = compute_team_strength(away_hist_indexed, avg_home, avg_away)

        prediction = predict_match(home_strength, away_strength, avg_home, avg_away)

        outcomes = [
            ("home", prediction.prob_home_win, match.pre_match_home_odds, match.closing_home_odds),
            ("draw", prediction.prob_draw, match.pre_match_draw_odds, match.closing_draw_odds),
            ("away", prediction.prob_away_win, match.pre_match_away_odds, match.closing_away_odds),
        ]

        actual_outcome = (
            "home" if match.home_goals > match.away_goals
            else "away" if match.home_goals < match.away_goals
            else "draw"
        )

        for outcome_name, model_prob, pre_match_odds, closing_odds in outcomes:
            score = score_outcome(outcome_name, model_prob, pre_match_odds)

            if score.ev_pct >= min_ev_pct:
                won = (outcome_name == actual_outcome)
                profit_loss = flat_stake * (pre_match_odds - 1) if won else -flat_stake
                clv_pct = compute_clv(pre_match_odds, closing_odds)

                picks.append(BacktestPick(
                    match=match,
                    outcome=outcome_name,
                    model_probability=model_prob,
                    odds_used=pre_match_odds,
                    ev_pct=score.ev_pct,
                    clv_pct=clv_pct,
                    won=won,
                    stake=flat_stake,
                    profit_loss=profit_loss,
                ))

        # Only AFTER scoring this match do we add it to history - this
        # ordering is what prevents look-ahead bias.
        team_history[match.home_team].append(
            MatchRecord(match.home_goals, match.away_goals, was_home=True, games_ago=0)
        )
        team_history[match.away_team].append(
            MatchRecord(match.away_goals, match.home_goals, was_home=False, games_ago=0)
        )
        league_goals[league].append((match.home_goals, match.away_goals))

    return picks


def summarize(picks: list[BacktestPick]):
    if not picks:
        print("No picks were generated - either the threshold is too strict "
              "or there isn't enough historical data yet.")
        return

    total_staked = sum(p.stake for p in picks)
    total_pl = sum(p.profit_loss for p in picks)
    win_rate = sum(1 for p in picks if p.won) / len(picks)
    avg_clv = sum(p.clv_pct for p in picks) / len(picks)
    roi_pct = (total_pl / total_staked) * 100 if total_staked > 0 else 0

    print(f"\n{'=' * 60}")
    print("BACKTEST SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total picks:      {len(picks)}")
    print(f"Win rate:         {win_rate:.1%}")
    print(f"Total staked:     {total_staked:,.2f}")
    print(f"Total P/L:        {total_pl:+,.2f}")
    print(f"ROI:              {roi_pct:+.2f}%")
    print(f"Average CLV:      {avg_clv:+.2f} percentage points")
    print(f"\nReminder: hundreds of picks across multiple seasons are needed")
    print(f"before any of these numbers are statistically meaningful.")


if __name__ == "__main__":
    historical_matches = load_historical_data()
    picks = run_backtest(historical_matches)
    summarize(picks)
