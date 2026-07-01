"""
team_strength.py

Computes attack_strength and defense_strength for a team, using
exponentially recency-weighted historical match data, plus an optional
small head-to-head (H2H) adjustment.

These two numbers feed directly into the Poisson model in poisson_model.py.

IMPORTANT (look-ahead bias):
When calling these functions for a backtest, only pass in matches that
happened BEFORE the match you are predicting. Never include future games
in the history list. This module does not enforce that for you - the
caller is responsible for slicing history correctly.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MatchRecord:
    """One historical match from a team's perspective."""
    goals_for: int
    goals_against: int
    was_home: bool
    games_ago: int  # 0 = most recent match, 1 = second most recent, etc.


@dataclass
class H2HRecord:
    """One historical head-to-head meeting between two specific teams."""
    goals_for: int       # goals scored by the team we are computing strength for
    goals_against: int   # goals scored by the opponent
    games_ago: int        # 0 = most recent meeting


@dataclass
class TeamStrength:
    attack_strength: float
    defense_strength: float
    sample_size: int


def exponential_weighted_average(values: List[float], games_ago: List[int], decay: float = 0.9) -> float:
    """
    Computes a recency-weighted average. More recent values (lower games_ago)
    count more. decay=0.9 means each match back in time is worth 90% of the
    previous one's weight.

    Returns 0.0 if values is empty (caller should handle this - usually means
    "not enough data, fall back to league average").
    """
    if not values:
        return 0.0

    weights = [decay ** g for g in games_ago]
    weighted_sum = sum(v * w for v, w in zip(values, weights))
    weight_total = sum(weights)

    if weight_total == 0:
        return 0.0

    return weighted_sum / weight_total


def compute_h2h_adjustment(
    h2h_history: List[H2HRecord],
    decay: float = 0.85,
    weight: float = 0.07,
    min_meetings: int = 4,
) -> float:
    """
    Computes a small additive adjustment to expected goals based on H2H history.

    Returns 0.0 (no adjustment) if there are fewer than min_meetings recorded
    meetings - a couple of noisy data points should not move the model.

    The weight parameter controls how much influence H2H gets relative to the
    base attack/defense strengths. Keep this low (0.05-0.10) - H2H is a minor
    correction, not a primary signal. See system design discussion for why.
    """
    if len(h2h_history) < min_meetings:
        return 0.0

    goal_diffs = [r.goals_for - r.goals_against for r in h2h_history]
    games_ago = [r.games_ago for r in h2h_history]

    avg_goal_diff = exponential_weighted_average(goal_diffs, games_ago, decay)

    return avg_goal_diff * weight


def compute_team_strength(
    history: List[MatchRecord],
    league_avg_goals_scored: float,
    league_avg_goals_conceded: float,
    decay: float = 0.9,
    min_sample_size: int = 5,
) -> TeamStrength:
    """
    Computes attack_strength and defense_strength for one team from their
    match history, using exponential recency weighting.

    attack_strength > 1.0 means the team scores more than league average.
    defense_strength > 1.0 means the team CONCEDES more than league average
    (i.e. higher defense_strength = weaker defense - this matches the
    convention used in the expected-goals formula in poisson_model.py).

    If history has fewer than min_sample_size matches, returns neutral
    strength (1.0, 1.0) rather than a noisy estimate from too little data.
    This is a deliberate guardrail - early season or newly promoted teams
    should not get wild strength ratings from 1-2 games.
    """
    if len(history) < min_sample_size:
        return TeamStrength(attack_strength=1.0, defense_strength=1.0, sample_size=len(history))

    goals_for = [m.goals_for for m in history]
    goals_against = [m.goals_against for m in history]
    games_ago = [m.games_ago for m in history]

    avg_goals_for = exponential_weighted_average(goals_for, games_ago, decay)
    avg_goals_against = exponential_weighted_average(goals_against, games_ago, decay)

    attack_strength = avg_goals_for / league_avg_goals_scored if league_avg_goals_scored > 0 else 1.0
    defense_strength = avg_goals_against / league_avg_goals_conceded if league_avg_goals_conceded > 0 else 1.0

    return TeamStrength(
        attack_strength=attack_strength,
        defense_strength=defense_strength,
        sample_size=len(history),
    )


def apply_injury_discount(
    expected_goals_for: float,
    expected_goals_against: float,
    key_attacker_out: bool = False,
    starting_keeper_out: bool = False,
) -> tuple[float, float]:
    """
    Crude manual adjustment for confirmed injuries/suspensions. This is
    explicitly a heuristic patch, not a modeled effect - see system design
    discussion. The multipliers are rough and intended to acknowledge a
    known signal rather than precisely quantify it.

    Returns (adjusted_expected_goals_for, adjusted_expected_goals_against).
    """
    adj_for = expected_goals_for
    adj_against = expected_goals_against

    if key_attacker_out:
        adj_for *= 0.875  # midpoint of the 0.85-0.90 range discussed

    if starting_keeper_out:
        adj_against *= 1.125  # midpoint of the 1.10-1.15 range discussed

    return adj_for, adj_against
