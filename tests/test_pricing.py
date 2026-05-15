"""
Tests for runner_sim/market/pricing.py

Focus: the pure math functions. compute_baseline has no randomness;
compute_price_change_pct uses random.uniform for noise — tests that verify
direction (positive/negative) seed the RNG first to suppress the noise term.
compute_anchor_pull_pct and compute_total_price_change_pct are deterministic
where they don't delegate to the noise function.
"""

import random
import pytest

from runner_sim.market.pricing import (
    ANCHOR_STRENGTH,
    BASE_EXPECTATION,
    EXPECTED_DELTA_RANGE,
    STARTING_VALUATION,
    VALUATION_CR_PER_COUNTER,
    compute_anchor_pull_pct,
    compute_baseline,
    compute_price_change_pct,
    compute_total_price_change_pct,
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


class TestComputeAnchorPullPct:
    """The valuation mean-reversion term. Pure deterministic math — no RNG.

    Fair value derivation: anchor_price × (projected / STARTING_VALUATION),
    where projected = valuation + pending_delta × VALUATION_CR_PER_COUNTER.
    Pull% = ANCHOR_STRENGTH × (fair_value − price_before) / price_before × 100.
    """

    def test_zero_pull_at_starting_valuation_no_pending(self):
        """The week-0 condition: projected == STARTING_VALUATION means
        fair_value == anchor_price. If price_before == anchor_price, pull
        should be exactly zero for every company regardless of price."""
        for anchor in (450.0, 380.0, 300.0, 200.0):
            pct = compute_anchor_pull_pct(
                price_before=anchor,
                valuation=STARTING_VALUATION,
                pending_valuation_delta=0.0,
                anchor_price=anchor,
            )
            assert pct == pytest.approx(0.0, abs=1e-9)

    def test_positive_pull_when_undervalued(self):
        """Valuation up 20% → fair_value up 20% → if price_before is at the
        old fair value, the pull is positive (price gets dragged up)."""
        pct = compute_anchor_pull_pct(
            price_before=300.0,
            valuation=6000.0,                  # 20% above STARTING_VALUATION
            pending_valuation_delta=0.0,
            anchor_price=300.0,
        )
        # fair_value = 300 × (6000/5000) = 360; gap = 60/300 = 20%; pull = 0.05×20 = 1.0%
        assert pct == pytest.approx(ANCHOR_STRENGTH * 20.0, rel=1e-9)

    def test_negative_pull_when_overvalued(self):
        """Valuation down 20% → fair_value down 20% → pull is negative."""
        pct = compute_anchor_pull_pct(
            price_before=300.0,
            valuation=4000.0,
            pending_valuation_delta=0.0,
            anchor_price=300.0,
        )
        # fair_value = 300 × (4000/5000) = 240; gap = -60/300 = -20%; pull = -1.0%
        assert pct == pytest.approx(-ANCHOR_STRENGTH * 20.0, rel=1e-9)
        assert pct < 0

    def test_pending_delta_contributes_to_projected(self):
        """pending_valuation_delta × VALUATION_CR_PER_COUNTER is added to
        valuation when computing projected — so it should shift the pull
        the same way a same-sized direct valuation change would."""
        with_pending = compute_anchor_pull_pct(
            price_before=300.0,
            valuation=5000.0,
            pending_valuation_delta=50.0,   # +50 × 20 = +1000 → projected 6000
            anchor_price=300.0,
        )
        with_valuation = compute_anchor_pull_pct(
            price_before=300.0,
            valuation=6000.0,
            pending_valuation_delta=0.0,
            anchor_price=300.0,
        )
        assert with_pending == pytest.approx(with_valuation, rel=1e-9)

    def test_zero_price_before_returns_zero(self):
        """Calibration / floor safety: a non-positive price_before must not
        produce a divide-by-zero; the function returns 0.0 to skip the pull."""
        assert compute_anchor_pull_pct(0.0, 5000.0, 0.0, 300.0) == 0.0
        assert compute_anchor_pull_pct(-10.0, 5000.0, 0.0, 300.0) == 0.0

    def test_pull_is_proportional_to_gap_at_fixed_strength(self):
        """A 10% fair-value gap should produce half the pull of a 20% gap —
        the formula is linear in (fair_value − price_before) / price_before."""
        small = compute_anchor_pull_pct(300.0, 5500.0, 0.0, 300.0)   # 10% gap
        large = compute_anchor_pull_pct(300.0, 6000.0, 0.0, 300.0)   # 20% gap
        assert large == pytest.approx(2 * small, rel=1e-9)


class TestComputeTotalPriceChangePct:
    """Composition: total = performance (delegates to compute_price_change_pct)
    + anchor (passed in). The relationship must be exactly additive so the
    performance term stays calibration-equivalent when anchor=0."""

    def test_anchor_zero_matches_performance_alone(self):
        """Identical RNG seed → calling the combiner with anchor=0 must yield
        exactly the same number as compute_price_change_pct alone."""
        baseline = compute_baseline(3)

        random.seed(42)
        performance_only = compute_price_change_pct(baseline + 500.0, baseline)

        random.seed(42)
        combined = compute_total_price_change_pct(
            baseline + 500.0, baseline, anchor_pull_pct=0.0,
        )
        assert combined == pytest.approx(performance_only, rel=1e-12)

    def test_anchor_is_exact_additive_offset(self):
        """A non-zero anchor must shift the result by exactly that amount.
        Same RNG seed → difference between the two calls equals the anchor."""
        baseline = compute_baseline(3)

        random.seed(42)
        without_anchor = compute_total_price_change_pct(
            baseline, baseline, anchor_pull_pct=0.0,
        )
        random.seed(42)
        with_anchor = compute_total_price_change_pct(
            baseline, baseline, anchor_pull_pct=1.5,
        )
        assert with_anchor - without_anchor == pytest.approx(1.5, rel=1e-12)
