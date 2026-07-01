"""
ev_scoring.py

Converts bookmaker odds and the model's probability estimate into Expected
Value (EV), and applies the market-signal confidence adjustment based on
odds movement.

This is the formula that decides what counts as a "value bet" - see system
design discussion for the full reasoning.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class OutcomeScore:
    outcome: str  # 'home', 'draw', 'away'
    model_probability: float
    odds: float
    implied_probability: float
    ev_pct: float
    confidence_multiplier: float
    edge_score: float


def odds_to_implied_probability(decimal_odds: float) -> float:
    """
    implied_probability = 1 / decimal_odds

    e.g. odds of 2.50 -> implied probability = 40%
    """
    if decimal_odds <= 0:
        raise ValueError(f"decimal_odds must be positive, got {decimal_odds}")
    return 1.0 / decimal_odds


def compute_ev(model_probability: float, decimal_odds: float) -> float:
    """
    EV = (your_estimated_probability * decimal_odds) - 1

    Returns EV as a fraction (e.g. 0.144 = +14.4% EV). Positive means the
    bet has positive expected value according to your model. This number
    is the actual "why is this the best pick" answer - it's not a vibe,
    it's this calculation.
    """
    return (model_probability * decimal_odds) - 1.0


def compute_market_signal_multiplier(
    odds_at_first_seen: float,
    odds_now: float,
    favored_outcome: bool = True,
    max_boost: float = 1.15,
    max_penalty: float = 0.85,
) -> float:
    """
    Computes a confidence multiplier based on how odds have moved since
    they were first seen for this outcome.

    If odds are SHORTENING (decreasing) on an outcome we favor, that means
    the market is moving toward agreeing with us -> mild confidence boost.
    If odds are LENGTHENING (increasing) on an outcome we favor, the market
    is moving away from our view -> mild confidence penalty.

    This is intentionally a SMALL adjustment (capped at +/-15%), not a
    separate score - see system design discussion for why market signal
    should be a multiplier on EV, not an independent input with equal weight.

    Returns a multiplier to apply to the raw EV (e.g. 1.05 = 5% boost).
    """
    if odds_at_first_seen <= 0:
        return 1.0

    # Implied probability movement: if implied probability went UP
    # (odds went down), that's the market agreeing with a "this will happen" view
    prob_first_seen = odds_to_implied_probability(odds_at_first_seen)
    prob_now = odds_to_implied_probability(odds_now)

    movement = prob_now - prob_first_seen  # positive = market agrees more

    # Scale movement into a multiplier, capped at max_boost/max_penalty.
    # A 5 percentage point shift maps to roughly the cap - this scaling
    # factor (3.0) is a starting heuristic, tune it once you have real
    # CLV data to calibrate against.
    raw_multiplier = 1.0 + (movement * 3.0)

    return max(max_penalty, min(max_boost, raw_multiplier))


def score_outcome(
    outcome: str,
    model_probability: float,
    current_odds: float,
    odds_at_first_seen: Optional[float] = None,
) -> OutcomeScore:
    """
    Full scoring pipeline for one outcome (e.g. "home win" for one match).

    edge_score = EV * confidence_multiplier

    If odds_at_first_seen is not provided (e.g. this is the first time we've
    seen this fixture), confidence_multiplier defaults to 1.0 (neutral -
    no market signal data yet).
    """
    implied_prob = odds_to_implied_probability(current_odds)
    ev = compute_ev(model_probability, current_odds)

    if odds_at_first_seen is not None:
        confidence_multiplier = compute_market_signal_multiplier(
            odds_at_first_seen, current_odds
        )
    else:
        confidence_multiplier = 1.0

    edge_score = ev * confidence_multiplier

    return OutcomeScore(
        outcome=outcome,
        model_probability=model_probability,
        odds=current_odds,
        implied_probability=implied_prob,
        ev_pct=ev * 100,
        confidence_multiplier=confidence_multiplier,
        edge_score=edge_score,
    )


def compute_clv(odds_at_bet_time: float, closing_odds: float) -> float:
    """
    CLV% = closing_implied_probability - your_implied_probability_at_bet_time

    Expressed in percentage points. Positive CLV means the market moved
    toward your position after you bet - the signature of real predictive
    signal. See system design discussion for the full worked example.
    """
    prob_at_bet = odds_to_implied_probability(odds_at_bet_time)
    prob_at_close = odds_to_implied_probability(closing_odds)
    return (prob_at_close - prob_at_bet) * 100
