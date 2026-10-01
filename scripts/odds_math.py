"""
Pure math helpers: median consensus lines, American-odds <-> implied
probability, no-vig win probability, and movement calculations. No network
or file I/O in this module -- keep it easy to unit test.
"""
from __future__ import annotations

import statistics


def median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def american_to_implied_prob(odds: float) -> float:
    """American odds -> raw (vig-included) implied win probability."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return (-odds) / ((-odds) + 100.0)


def no_vig_prob(home_odds: float, away_odds: float) -> tuple[float, float] | None:
    """Two-way no-vig implied probabilities for (home, away) from their
    American moneyline prices. Returns None if either price is missing."""
    if home_odds is None or away_odds is None:
        return None
    p_home = american_to_implied_prob(home_odds)
    p_away = american_to_implied_prob(away_odds)
    total = p_home + p_away
    if total <= 0:
        return None
    return p_home / total, p_away / total


def consensus_spread_or_total(book_lines: list[float]) -> float | None:
    """Consensus line for a spread or total market: median of the posted
    lines across books that currently have a price up."""
    return median(book_lines)


def consensus_moneyline_prob(book_pairs: list[tuple[float, float]]) -> float | None:
    """Consensus home win probability for a moneyline market: the median of
    each book's own no-vig home probability (not the no-vig of the median
    prices -- medianing first and devigging after would let a single
    aggressive book skew which price gets picked)."""
    probs = []
    for home_odds, away_odds in book_pairs:
        result = no_vig_prob(home_odds, away_odds)
        if result:
            probs.append(result[0])
    return median(probs)


def points_movement(current: float | None, reference: float | None) -> float | None:
    """Movement in points for a spread/total, current minus reference."""
    if current is None or reference is None:
        return None
    return round(current - reference, 2)


def prob_movement_pct_points(current_prob: float | None, reference_prob: float | None) -> float | None:
    """Movement in no-vig implied win probability, expressed in percentage
    points (so +3.5 means the consensus home win probability rose 3.5pp)."""
    if current_prob is None or reference_prob is None:
        return None
    return round((current_prob - reference_prob) * 100.0, 2)
