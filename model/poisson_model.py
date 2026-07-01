"""
poisson_model.py

Converts two teams' attack/defense strengths into expected goals for a
specific match, then builds a full scoreline probability grid using the
Poisson distribution, and collapses that grid into home/draw/away
probabilities.

This is the core prediction engine. See team_strength.py for how the
attack_strength/defense_strength inputs are computed.
"""

from dataclasses import dataclass
from scipy.stats import poisson

from .team_strength import TeamStrength


@dataclass
class MatchPrediction:
    expected_goals_home: float
    expected_goals_away: float
    prob_home_win: float
    prob_draw: float
    prob_away_win: float
    scoreline_grid: dict  # {(home_goals, away_goals): probability}


def compute_expected_goals(
    home_team: TeamStrength,
    away_team: TeamStrength,
    league_avg_home_goals: float,
    league_avg_away_goals: float,
) -> tuple[float, float]:
    """
    Computes expected goals for each side in this specific match.

    Formula (see system design discussion):
        expected_goals_home = league_avg_home_goals * home.attack_strength * away.defense_strength
        expected_goals_away = league_avg_away_goals * away.attack_strength * home.defense_strength

    A strong attack (high attack_strength) facing a weak defense (high
    defense_strength, meaning they concede a lot) multiplies up. A strong
    attack facing a strong defense (low defense_strength) gets pulled down.
    """
    expected_goals_home = (
        league_avg_home_goals * home_team.attack_strength * away_team.defense_strength
    )
    expected_goals_away = (
        league_avg_away_goals * away_team.attack_strength * home_team.defense_strength
    )
    return expected_goals_home, expected_goals_away


def build_scoreline_grid(
    expected_goals_home: float,
    expected_goals_away: float,
    max_goals: int = 7,
    rho: float = 0.0,
) -> dict:
    """
    Builds the full scoreline probability grid using independent Poisson
    distributions for home and away goals, multiplied together.

    P(home=h, away=a) = P_home(h) * P_away(a)

    max_goals=7 covers 0-6 goals per side (7 values: 0,1,2,3,4,5,6) which
    captures effectively all realistic scorelines - probabilities beyond
    6 goals are negligible for almost all matches.

    rho: optional Dixon-Coles low-score correlation correction. Real matches
    have slightly more 0-0, 1-0, 0-1, and 1-1 results than pure independent
    Poisson predicts. Leave at 0.0 (no correction) for the basic model;
    a small negative value (e.g. -0.05 to -0.15, fit from historical data)
    applies the correction. This is a v2 refinement - see system design
    discussion for why it's lower priority than recency weighting and H2H.
    """
    grid = {}

    for h in range(max_goals):
        for a in range(max_goals):
            p_home = poisson.pmf(h, expected_goals_home)
            p_away = poisson.pmf(a, expected_goals_away)
            joint_prob = p_home * p_away

            if rho != 0.0 and h <= 1 and a <= 1:
                tau = _dixon_coles_tau(h, a, rho)
                joint_prob *= tau

            grid[(h, a)] = joint_prob

    # Renormalize so probabilities sum to 1.0 (the tau adjustment and the
    # truncation at max_goals both introduce tiny deviations from exactly 1.0)
    total = sum(grid.values())
    if total > 0:
        grid = {k: v / total for k, v in grid.items()}

    return grid


def _dixon_coles_tau(h: int, a: int, rho: float) -> float:
    """
    Dixon-Coles correlation adjustment for low-scoring outcomes only.
    Only applies to (0,0), (1,0), (0,1), (1,1) - all other scorelines
    are untouched (tau = 1.0).
    """
    if h == 0 and a == 0:
        return 1 - rho
    elif h == 1 and a == 1:
        return 1 - rho
    elif (h == 0 and a == 1) or (h == 1 and a == 0):
        return 1 + rho
    return 1.0


def collapse_to_outcome_probabilities(grid: dict) -> tuple[float, float, float]:
    """
    Sums the scoreline grid into home win / draw / away win probabilities.

        P(home win) = sum of all cells where h > a
        P(draw)     = sum of all cells where h == a
        P(away win) = sum of all cells where h < a
    """
    prob_home_win = sum(p for (h, a), p in grid.items() if h > a)
    prob_draw = sum(p for (h, a), p in grid.items() if h == a)
    prob_away_win = sum(p for (h, a), p in grid.items() if h < a)

    return prob_home_win, prob_draw, prob_away_win


def predict_match(
    home_team: TeamStrength,
    away_team: TeamStrength,
    league_avg_home_goals: float,
    league_avg_away_goals: float,
    h2h_adjustment: float = 0.0,
    rho: float = 0.0,
) -> MatchPrediction:
    """
    Full pipeline: team strengths -> expected goals -> scoreline grid ->
    outcome probabilities. This is the main entry point most callers should use.

    h2h_adjustment is added directly to expected_goals_home (a positive
    value means the home team's H2H record against this specific opponent
    is better than their general form suggests). See team_strength.py's
    compute_h2h_adjustment() for how to calculate this value.
    """
    expected_goals_home, expected_goals_away = compute_expected_goals(
        home_team, away_team, league_avg_home_goals, league_avg_away_goals
    )

    expected_goals_home += h2h_adjustment

    # Expected goals cannot be negative - floor at a small positive value
    expected_goals_home = max(expected_goals_home, 0.05)
    expected_goals_away = max(expected_goals_away, 0.05)

    grid = build_scoreline_grid(expected_goals_home, expected_goals_away, rho=rho)
    prob_home_win, prob_draw, prob_away_win = collapse_to_outcome_probabilities(grid)

    return MatchPrediction(
        expected_goals_home=expected_goals_home,
        expected_goals_away=expected_goals_away,
        prob_home_win=prob_home_win,
        prob_draw=prob_draw,
        prob_away_win=prob_away_win,
        scoreline_grid=grid,
    )
