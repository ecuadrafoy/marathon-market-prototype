"""
Market pricing — converts per-company total credits extracted into a
weekly stock price change.

The new formula compares actual total credits vs a calibrated baseline
expectation, normalizing by the typical weekly stddev. Constants are
re-derived after integration via runner_sim.market.calibration.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# Calibrated via runner_sim.market.calibration.headless_calibration(weeks=1000, seed=42)
# Re-run calibration whenever roster size, zone count, item catalog, or
# runner-attribute generation changes meaningfully.
# ---------------------------------------------------------------------------
BASE_EXPECTATION     = 408.83  # per-squad expected credits (mean / 3 squads)
EXPECTED_DELTA_RANGE = 634.06  # typical weekly stddev of per-company total credits
DELTA_MULTIPLIER     = 10.0    # stretches normalized delta to ±10% range
NOISE_RANGE          = 2.0     # weekly ±2% random noise on price change
PRICE_FLOOR          = 1.0     # prices never go below this


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
    price_change_pct: float = 0.0
    price_before: float = 0.0
    price_after: float = 0.0
    # monitored zone intel (Sector 7 only)
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
