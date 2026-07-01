"""
picks_selector.py

Takes scored outcomes across all of today's fixtures and filters down to
the top 2-5 picks, applying a minimum EV threshold and deduplicating so
multiple outcomes from the same match don't all get sent.

This is the final step before Telegram delivery.
"""

from dataclasses import dataclass
from typing import List

from .ev_scoring import OutcomeScore


@dataclass
class FixtureOutcomeScore:
    """An OutcomeScore tagged with which fixture it belongs to, so the
    selector can dedupe per match."""
    fixture_id: int
    home_team: str
    away_team: str
    score: OutcomeScore


def select_top_picks(
    all_scores: List[FixtureOutcomeScore],
    min_ev_pct: float = 3.0,
    max_picks: int = 5,
    min_picks_target: int = 2,
) -> List[FixtureOutcomeScore]:
    """
    Filters and ranks scored outcomes down to the final daily picks list.

    Steps:
    1. Filter out anything below min_ev_pct (default +3% EV) - this is the
       "no matter how confident it feels, if EV isn't positive enough, skip it"
       rule from the system design.
    2. Deduplicate per fixture - only the single best-scoring outcome from
       each match survives, so you never get e.g. both "home win" and "draw"
       flagged for the same game.
    3. Rank by edge_score descending.
    4. Take the top max_picks (default 5).

    Returns an empty list on days where nothing clears the threshold - this
    is expected and correct behavior, not a bug. A system that always finds
    2-5 "good" bets every single day, regardless of how the day's matches
    actually price out, is a sign something is wrong (see the EV discussion
    on why guaranteed daily picks isn't realistic). Some days may have 0,
    1, or 2 picks; some may have the full 5. min_picks_target is informational
    only and does not change the filtering logic.
    """
    # Step 1: filter by minimum EV
    above_threshold = [
        fs for fs in all_scores if fs.score.ev_pct >= min_ev_pct
    ]

    # Step 2: dedupe per fixture - keep only the best edge_score per fixture_id
    best_per_fixture: dict[int, FixtureOutcomeScore] = {}
    for fs in above_threshold:
        existing = best_per_fixture.get(fs.fixture_id)
        if existing is None or fs.score.edge_score > existing.score.edge_score:
            best_per_fixture[fs.fixture_id] = fs

    deduped = list(best_per_fixture.values())

    # Step 3: rank by edge_score descending
    ranked = sorted(deduped, key=lambda fs: fs.score.edge_score, reverse=True)

    # Step 4: take top N
    return ranked[:max_picks]


def format_pick_summary(fs: FixtureOutcomeScore) -> str:
    """
    Human-readable one-line summary of a pick, useful for logging/debugging
    before wiring up the actual Telegram message formatting.
    """
    s = fs.score
    return (
        f"{fs.home_team} vs {fs.away_team} | "
        f"{s.outcome} @ {s.odds:.2f} | "
        f"model: {s.model_probability:.1%} vs implied: {s.implied_probability:.1%} | "
        f"EV: {s.ev_pct:+.1f}% | edge_score: {s.edge_score:+.3f}"
    )
