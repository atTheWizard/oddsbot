"""
test_pipeline.py

End-to-end smoke test: takes a handful of made-up fixtures with team
strengths and bookmaker odds, runs them through the full chain
(Poisson model -> EV scoring -> picks selector), and prints the result.

This is NOT the backtest (that requires real historical data and strict
chronological ordering - see system design discussion). This just proves
the logic in team_strength.py, poisson_model.py, ev_scoring.py, and
picks_selector.py correctly connects end to end before wiring up a real
odds API.
"""

from model.team_strength import TeamStrength
from model.poisson_model import predict_match
from model.ev_scoring import score_outcome
from model.picks_selector import FixtureOutcomeScore, select_top_picks, format_pick_summary

LEAGUE_AVG_HOME_GOALS = 1.5
LEAGUE_AVG_AWAY_GOALS = 1.2

# Made-up sample fixtures: (fixture_id, home_name, away_name, home_strength, away_strength, odds)
# odds = (home_odds, draw_odds, away_odds) - imagine these came from the bookmaker
sample_fixtures = [
    (
        1, "Riverside FC", "Oakdale United",
        TeamStrength(attack_strength=1.4, defense_strength=0.9, sample_size=10),
        TeamStrength(attack_strength=0.8, defense_strength=1.1, sample_size=10),
        (1.65, 3.80, 5.50),  # bookmaker underpricing home win relative to model? let's see
    ),
    (
        2, "Northgate City", "Westbrook Athletic",
        TeamStrength(attack_strength=1.05, defense_strength=1.0, sample_size=12),
        TeamStrength(attack_strength=1.1, defense_strength=0.95, sample_size=12),
        (2.60, 3.30, 2.70),  # close match, fairly priced
    ),
    (
        3, "Lowmoor Town", "Castlebridge Rovers",
        TeamStrength(attack_strength=0.7, defense_strength=1.3, sample_size=9),
        TeamStrength(attack_strength=1.5, defense_strength=0.75, sample_size=9),
        (4.20, 3.60, 1.75),  # strong away favorite
    ),
    (
        4, "Fernhill Rangers", "Eastport Wanderers",
        TeamStrength(attack_strength=1.2, defense_strength=0.85, sample_size=11),
        TeamStrength(attack_strength=0.9, defense_strength=1.05, sample_size=11),
        (1.90, 3.50, 4.00),
    ),
]

all_scores = []

print("=" * 90)
print("MODEL PREDICTIONS vs BOOKMAKER ODDS")
print("=" * 90)

for fixture_id, home_name, away_name, home_strength, away_strength, odds in sample_fixtures:
    home_odds, draw_odds, away_odds = odds

    prediction = predict_match(
        home_team=home_strength,
        away_team=away_strength,
        league_avg_home_goals=LEAGUE_AVG_HOME_GOALS,
        league_avg_away_goals=LEAGUE_AVG_AWAY_GOALS,
    )

    print(f"\n{home_name} vs {away_name}")
    print(f"  Expected goals: {prediction.expected_goals_home:.2f} - {prediction.expected_goals_away:.2f}")
    print(f"  Model probs:   home {prediction.prob_home_win:.1%} | draw {prediction.prob_draw:.1%} | away {prediction.prob_away_win:.1%}")
    print(f"  Bookmaker odds: home {home_odds} | draw {draw_odds} | away {away_odds}")

    outcomes = [
        ("home", prediction.prob_home_win, home_odds),
        ("draw", prediction.prob_draw, draw_odds),
        ("away", prediction.prob_away_win, away_odds),
    ]

    for outcome_name, model_prob, outcome_odds in outcomes:
        score = score_outcome(
            outcome=outcome_name,
            model_probability=model_prob,
            current_odds=outcome_odds,
        )
        print(f"    {outcome_name:5s}: EV {score.ev_pct:+6.1f}%  (model {model_prob:.1%} vs implied {score.implied_probability:.1%})")

        all_scores.append(
            FixtureOutcomeScore(
                fixture_id=fixture_id,
                home_team=home_name,
                away_team=away_name,
                score=score,
            )
        )

print("\n" + "=" * 90)
print("SELECTED PICKS (EV >= 3%, top 5, one per fixture)")
print("=" * 90)

picks = select_top_picks(all_scores, min_ev_pct=3.0, max_picks=5)

if not picks:
    print("\nNo picks cleared the threshold today. This is expected behavior,")
    print("not a bug - see picks_selector.py docstring.")
else:
    for i, pick in enumerate(picks, 1):
        print(f"\n{i}. {format_pick_summary(pick)}")
