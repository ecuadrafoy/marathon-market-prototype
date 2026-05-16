"""
Market pricing — converts per-company state into a weekly stock price change.

The weekly price move has two additive components:

  price_change_pct = performance_pct   (extraction credits vs. calibrated baseline
                                        + noise — the DOMINANT, hidden-driven term)
                   + anchor_pull_pct   (gentle mean-reversion toward a valuation-
                                        derived "fair value" — a minor, visible term)

The performance term is calibrated in isolation (anchor excluded) via
runner_sim.market.calibration. The anchor term is purely additive on top, so
the two stay cleanly separable: recalibration never touches the anchor, and
the anchor never invalidates the calibration.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
# Performance term — calibrated via
# runner_sim.market.calibration.headless_calibration(weeks=1000, seed=42).
# Re-run calibration whenever roster size, zone count, item catalog, or
# runner-attribute generation changes meaningfully.
BASE_EXPECTATION     = 120.0   # per-squad expected credits (= MEDIAN of active company-weeks / 3
                               # under the company-AI loop; sat-out weeks excluded).
                               # Median, not mean — loot is right-skewed (rare Epic items pull
                               # the mean up). The typical week sits at the median, so the
                               # median is what makes the formula treat a typical week as
                               # neutral. See calibration.headless_calibration (seed=42).
                               # Was 408.83 under the old fixed-9-runner-roster model.
EXPECTED_DELTA_RANGE = 826.0   # weekly stddev of per-company total credits. Wider than the
                               # old 634.06 — variable rosters introduce more variance between
                               # low-roster and full-roster weeks.
DELTA_MULTIPLIER     = 10.0    # stretches normalized delta to ±10% range
NOISE_RANGE          = 2.0     # weekly ±2% random noise on price change
PRICE_FLOOR          = 1.0     # prices never go below this

# Valuation constants — owned here (the single source of truth) so this module
# has no upward import of marathon_market. marathon_market.py re-imports these.
STARTING_VALUATION       = 5_000.0  # every company's week-0 enterprise valuation
VALUATION_CR_PER_COUNTER = 20.0     # credits per pending counter-score point at the quarterly report

# Anchor term — gentle weekly mean-reversion toward valuation-derived fair value.
# 0.05 → ~14-week drift-correction half-life; with a 20% fair-value gap the
# weekly pull is ~1%, well dominated by the ±~10% performance wobble. The anchor
# is mean-reverting (negative feedback) so it dampens, not amplifies, spirals.
ANCHOR_STRENGTH = 0.05


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class CompanyWeekResult:
    company_name: str
    # squad-level aggregates (across all zones)
    squads_deployed: int = 3
    squads_returned: int = 0
    squads_eliminated: int = 0
    total_credits_extracted: float = 0.0
    total_eliminations: int = 0
    # market math
    baseline: float = 0.0
    delta: float = 0.0
    price_change_pct: float = 0.0       # total move = performance_pct + anchor_pull_pct
    performance_pct: float = 0.0        # the hidden extraction-driven component
    anchor_pull_pct: float = 0.0        # the valuation mean-reversion component
    fair_value: float = 0.0             # valuation-derived price the anchor pulls toward
    price_before: float = 0.0
    price_after: float = 0.0
    # monitored zone intel (Perimeter only)
    monitored_squad_returned: bool = False
    monitored_credits: float = 0.0
    monitored_eliminations: int = 0
    monitored_runner_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MARKET MATH
# ---------------------------------------------------------------------------
def compute_baseline(squads_deployed: int) -> float:
    """Per-week per-company expectation: BASE_EXPECTATION × number of squads.

    With the v1 design (squads_deployed always 3), this simplifies to
    `3 * BASE_EXPECTATION`. Keeping the parameter explicit makes future
    variable-squad-counts a one-line change.
    """
    return BASE_EXPECTATION * squads_deployed


def compute_price_change_pct(total_credits: float, baseline: float) -> float:
    """Convert total credits → weekly stock price change percent.

    The signal:
      delta      = total_credits - baseline      (raw over/underperformance)
      normalized = delta / EXPECTED_DELTA_RANGE  (≈ stddev → unit-ish scale)
      pct        = normalized * DELTA_MULTIPLIER + uniform_noise

    EXPECTED_DELTA_RANGE comes from headless_calibration: the stddev of
    per-company-week credit totals. A "typical good week" lands at ≈ +1.0
    normalized → +10% before noise; "typical bad week" at ≈ -10%.
    """
    delta = total_credits - baseline
    normalized = delta / EXPECTED_DELTA_RANGE
    noise = random.uniform(-NOISE_RANGE, NOISE_RANGE)
    return (normalized * DELTA_MULTIPLIER) + noise


def compute_anchor_pull_pct(
    price_before: float,
    valuation: float,
    pending_valuation_delta: float,
    anchor_price: float,
) -> float:
    """Gentle weekly mean-reversion toward a valuation-derived fair value.

    The "fair value" is each company's own week-0 price scaled by how far its
    *projected* valuation has moved from the starting valuation:

        projected  = valuation + pending_valuation_delta × VALUATION_CR_PER_COUNTER
        fair_value = anchor_price × (projected / STARTING_VALUATION)
        pull%      = ANCHOR_STRENGTH × (fair_value − price_before) / price_before × 100

    Anchoring to each company's *own* starting price (rather than a global
    divisor) preserves the deliberate inter-company price spread: at week 0
    projected == STARTING_VALUATION, so fair_value == anchor_price and the pull
    is exactly zero for every company regardless of its price.

    Returns 0.0 when price_before is non-positive (calibration-mode / floor
    safety) — the anchor simply doesn't apply.
    """
    if price_before <= 0.0:
        return 0.0
    projected = valuation + pending_valuation_delta * VALUATION_CR_PER_COUNTER
    fair_value = anchor_price * (projected / STARTING_VALUATION)
    return ANCHOR_STRENGTH * (fair_value - price_before) / price_before * 100.0


def compute_total_price_change_pct(
    total_credits: float,
    baseline: float,
    *,
    anchor_pull_pct: float = 0.0,
) -> float:
    """The full weekly price move: performance term + anchor term.

    Keeping this separate from compute_price_change_pct means the performance
    term stays calibrated in isolation — calibration measures the output of
    compute_price_change_pct alone, and the anchor is layered on additively.
    """
    return compute_price_change_pct(total_credits, baseline) + anchor_pull_pct
