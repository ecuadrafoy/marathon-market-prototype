"""
Tests for runner_sim/market/pricing.py

Focus: the two pure math functions. compute_baseline has no randomness;
compute_price_change_pct uses random.uniform for noise — tests that verify
direction (positive/negative) seed the RNG first to suppress the noise term.
"""

import random
import pytest

from runner_sim.market.pricing import (
    BASE_EXPECTATION,
    EXPECTED_DELTA_RANGE,
    compute_baseline,
    compute_price_change_pct,
)


class TestComputeBaseline:
    def test_three_squads_equals_three_times_expectation(self):
        """Standard case: 3 squads × BASE_EXPECTATION."""
        assert compute_baseline(3) == pytest.approx(BASE_EXPECTATION * 3)

    def test_scales_linearly_with_squad_count(self):
        """Baseline grows proportionally — doubling squads doubles the bar."""
        assert compute_baseline(6) == pytest.approx(compute_baseline(3) * 2)

    def test_zero_squads_returns_zero(self):
        assert compute_baseline(0) == 0.0


class TestComputePriceChangePct:
    def test_above_baseline_is_positive(self):
        """Extracting more than expected should produce a positive price change.

        With delta = +EXPECTED_DELTA_RANGE, normalized = 1.0, so the signal
        is +10% before noise. Use seed=0 which gives noise ≈ +0.7% — safely positive.
        """
        random.seed(0)
        baseline = compute_baseline(3)
        pct = compute_price_change_pct(baseline + EXPECTED_DELTA_RANGE, baseline)
        assert pct > 0

    def test_below_baseline_is_negative(self):
        """Extracting less than expected should produce a negative price change.

        With total_credits=0, delta = -baseline (large negative), so the signal
        is deeply negative. Noise (±2%) cannot flip the sign here.
        """
        random.seed(0)
        baseline = compute_baseline(3)
        pct = compute_price_change_pct(0.0, baseline)
        assert pct < 0

    def test_at_baseline_is_near_zero(self):
        """Meeting expectations exactly produces only noise (±2%)."""
        random.seed(42)
        baseline = compute_baseline(3)
        pct = compute_price_change_pct(baseline, baseline)
        assert abs(pct) <= 2.5   # noise is ±2%, tiny margin for float drift
