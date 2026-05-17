"""
Tests for runner_sim/market/company_strategy.py

Covers each public function in the v1 company-AI module:
  - compute_upkeep        — earned-value formula, three orthogonal axes
  - settle_payroll        — cheapest-first ordering, orphan semantics, affinity preserved
  - company_health        — 3-week MA boundaries
  - decide_voluntary_drops— struggling drops > 2× median; thriving keeps everyone
  - decide_acquisitions   — health-driven preference & bid amount
  - resolve_bidding       — sequential draft with budget contention
  - tick_free_agent_pool  — retirement at 8 weeks; rookie spawn floor
  - release_to_free_agents— preserves career stats and affinities
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field

import pytest

from runner_sim.runners import Runner
from runner_sim.market.roster import CompanyRoster, create_roster
from runner_sim.market.shell_market import make_initial_market
from runner_sim.market.company_strategy import (
    BASE_UPKEEP,
    CREDIT_SHARE_TO_COMPANY,
    CompanyMemory,
    INITIAL_FREE_AGENT_BENCH,
    LOAN_AMOUNT,
    LOAN_REPAY_BUDGET_THRESHOLD,
    LOAN_TERM_WEEKS,
    LOAN_TRIGGER_BUDGET_THRESHOLD,
    Loan,
    MAX_OUTSTANDING_LOANS,
    MEMORY_WINDOW_WEEKS,
    MIN_GLOBAL_POOL,
    ORPHAN_RETIRE_AFTER_WEEKS,
    PostureState,
    RISK_APPETITE_STEP,
    RISK_APPETITE_SWEEP_BONUS,
    RISK_APPETITE_WIPE_PENALTY,
    RunnerIdCounter,
    WeekSnapshot,
    auto_repay_loan,
    collect_company_income,
    company_health,
    compute_upkeep,
    decide_acquisitions,
    decide_voluntary_drops,
    outstanding_loans,
    overdue_loans,
    posture_to_health,
    refresh_upkeep,
    release_to_free_agents,
    resolve_bidding,
    settle_payroll,
    take_loan_if_needed,
    tick_free_agent_pool,
    update_posture,
)


# ---------------------------------------------------------------------------
# TEST HELPERS
# ---------------------------------------------------------------------------
@dataclass
class FakeCompany:
    """Minimal stand-in for marathon_market.Company to avoid import cycles."""
    name: str
    price: float = 100.0
    budget: float = 0.0
    loans: list = field(default_factory=list)
    posture: PostureState = field(default_factory=PostureState)
    memory: CompanyMemory = field(default_factory=CompanyMemory)


def _snap(
    week: int = 0,
    price_change_pct: float = 0.0,
    squads_deployed: int = 3,
    squads_returned: int = 2,
    squads_eliminated: int = 1,
    per_zone_credits: dict | None = None,
    per_zone_squads_deployed: dict | None = None,
    per_zone_squads_eliminated: dict | None = None,
) -> WeekSnapshot:
    """Helper: build a WeekSnapshot with defaults that match typical mixed weeks."""
    return WeekSnapshot(
        week=week,
        price_change_pct=price_change_pct,
        extracted_credits=0.0,
        squads_deployed=squads_deployed,
        squads_returned=squads_returned,
        squads_eliminated=squads_eliminated,
        per_zone_credits=per_zone_credits or {},
        per_zone_squads_deployed=per_zone_squads_deployed or {},
        per_zone_squads_eliminated=per_zone_squads_eliminated or {},
        deaths=0,
        budget_delta=0.0,
        roster_size_after=9,
    )


def _record_and_update(
    posture: PostureState,
    memory: CompanyMemory,
    *,
    price_change_pct: float,
    squads_deployed: int,
    squads_returned: int,
    squads_eliminated: int,
) -> None:
    """Test convenience: record a snapshot then update posture from it."""
    memory.record(_snap(
        week=posture.weeks_observed,
        price_change_pct=price_change_pct,
        squads_deployed=squads_deployed,
        squads_returned=squads_returned,
        squads_eliminated=squads_eliminated,
    ))
    update_posture(posture, memory)


def _make_runner(
    runner_id: int = 1,
    name: str = "Test",
    company_name: str = "TestCo",
    shell: str = "Recon",
    net_loot: float = 0.0,
    eliminations: int = 0,
    deployments_survived: int = 0,
    affinities: dict | None = None,
) -> Runner:
    r = Runner(
        id=runner_id,
        name=name,
        company_name=company_name,
        combat=0.33, extraction=0.34, support=0.33,
        current_shell=shell,
    )
    r.net_loot = net_loot
    r.eliminations = eliminations
    r.deployments_survived = deployments_survived
    if affinities is not None:
        for shell_name, value in affinities.items():
            r.shell_affinities[shell_name] = value
    return r


def _populate_roster(runners: list[Runner]) -> CompanyRoster:
    roster = CompanyRoster(company_name="TestCo")
    roster.runners = runners
    return roster


# ---------------------------------------------------------------------------
# compute_upkeep — three orthogonal axes of earned value
# ---------------------------------------------------------------------------
class TestComputeUpkeep:
    def test_rookie_costs_base(self):
        """A brand-new runner with zero career stats costs exactly BASE_UPKEEP."""
        r = _make_runner()
        assert compute_upkeep(r) == pytest.approx(BASE_UPKEEP)

    def test_monotonic_in_net_loot(self):
        a = _make_runner(net_loot=0.0)
        b = _make_runner(net_loot=2000.0)
        assert compute_upkeep(b) > compute_upkeep(a)

    def test_monotonic_in_eliminations(self):
        a = _make_runner(eliminations=0)
        b = _make_runner(eliminations=10)
        assert compute_upkeep(b) > compute_upkeep(a)

    def test_monotonic_in_deployments_survived(self):
        """Longevity axis — orthogonal to combat/extraction."""
        a = _make_runner(deployments_survived=0)
        b = _make_runner(deployments_survived=20)
        assert compute_upkeep(b) > compute_upkeep(a)

    def test_long_serving_support_is_valuable_without_combat(self):
        """The motivating case for adding deployments_survived to the formula:
        a Triage who's never gotten a kill or extracted much can still command
        meaningful upkeep just by *not dying*."""
        veteran_support = _make_runner(
            shell="Triage",
            net_loot=500.0,        # barely any extraction credit
            eliminations=1,         # almost no kills
            deployments_survived=35,
        )
        assert compute_upkeep(veteran_support) > 100.0

    def test_elite_costs_substantially_more_than_rookie(self):
        elite = _make_runner(
            shell="Destroyer",
            net_loot=8000.0,
            eliminations=30,
            deployments_survived=40,
        )
        rookie = _make_runner()
        assert compute_upkeep(elite) > 250.0
        assert compute_upkeep(elite) > 10 * compute_upkeep(rookie)

    def test_shell_does_not_affect_upkeep(self):
        """Earned-value formula: a Destroyer and a Recon with identical stats
        cost the same. No shell-tier multiplier in v1."""
        destroyer = _make_runner(shell="Destroyer", net_loot=2000, eliminations=8)
        recon     = _make_runner(shell="Recon",     net_loot=2000, eliminations=8)
        assert compute_upkeep(destroyer) == pytest.approx(compute_upkeep(recon))


# ---------------------------------------------------------------------------
# settle_payroll — cheapest first, orphans preserve affinity
# ---------------------------------------------------------------------------
class TestSettlePayroll:
    def test_all_kept_when_budget_covers_everyone(self):
        company = FakeCompany("TestCo", budget=300.0)
        r1 = _make_runner(runner_id=1, net_loot=0)             # ~15
        r2 = _make_runner(runner_id=2, net_loot=2000)          # ~45
        r3 = _make_runner(runner_id=3, net_loot=5000)          # ~90
        roster = _populate_roster([r1, r2, r3])

        kept, orphaned = settle_payroll(company, roster)
        assert orphaned == []
        assert {r.id for r in kept} == {1, 2, 3}
        assert company.budget < 300.0
        assert company.budget > 0   # didn't spend everything

    def test_orphans_the_unaffordable_when_budget_tight(self):
        """Only the cheapest fit; the expensive runner is orphaned."""
        company = FakeCompany("TestCo", budget=80.0)
        cheap1 = _make_runner(runner_id=1, net_loot=0)         # ~15
        cheap2 = _make_runner(runner_id=2, net_loot=2000)      # ~45
        expensive = _make_runner(runner_id=3, eliminations=50, deployments_survived=50)  # ~215
        roster = _populate_roster([cheap1, cheap2, expensive])

        kept, orphaned = settle_payroll(company, roster)
        assert {r.id for r in kept} == {1, 2}
        assert [r.id for r in orphaned] == [3]
        assert roster.runners == kept   # roster mutated to match kept list

    def test_orphaned_runner_keeps_shell_affinities(self):
        """The whole point: affinity is preserved on orphaning. A rival can
        sign this runner and immediately benefit from their specialization."""
        company = FakeCompany("TestCo", budget=20.0)  # only covers 1 runner
        v = _make_runner(runner_id=1, net_loot=8000, eliminations=30, deployments_survived=40,
                         affinities={"Destroyer": 0.8, "Assassin": 0.3})
        cheap = _make_runner(runner_id=2)
        roster = _populate_roster([v, cheap])

        _kept, orphaned = settle_payroll(company, roster)
        assert orphaned[0].id == 1
        assert orphaned[0].shell_affinities["Destroyer"] == pytest.approx(0.8)
        assert orphaned[0].shell_affinities["Assassin"] == pytest.approx(0.3)
        assert orphaned[0].net_loot == 8000  # career stats also intact

    def test_cheapest_first_ordering(self):
        """One elite cannot bankrupt the entire roster. If we paid expensive-
        first, a single high-upkeep runner could eat the whole budget."""
        company = FakeCompany("TestCo", budget=50.0)
        elite = _make_runner(runner_id=1, eliminations=50, deployments_survived=50)  # ~215
        cheap1 = _make_runner(runner_id=2)
        cheap2 = _make_runner(runner_id=3)
        roster = _populate_roster([elite, cheap1, cheap2])

        kept, orphaned = settle_payroll(company, roster)
        # Cheapest-first: both cheaps stay, elite is orphaned.
        assert {r.id for r in kept} == {2, 3}
        assert [r.id for r in orphaned] == [1]


# ---------------------------------------------------------------------------
# company_health — 3-week MA boundary
# ---------------------------------------------------------------------------
class TestCompanyHealth:
    def test_thriving_when_above_threshold(self):
        history = [100.0, 100.0, 100.0, 110.0]
        assert company_health(history) == "thriving"

    def test_struggling_when_below_threshold(self):
        history = [100.0, 100.0, 100.0, 90.0]
        assert company_health(history) == "struggling"

    def test_neutral_inside_band(self):
        history = [100.0, 100.0, 100.0, 101.0]
        assert company_health(history) == "neutral"

    def test_neutral_with_insufficient_history(self):
        assert company_health([100.0]) == "neutral"
        assert company_health([100.0, 100.0]) == "neutral"

    def test_neutral_when_ma_is_zero(self):
        """Defensive: a zero-price MA shouldn't crash (rare PRICE_FLOOR edge)."""
        assert company_health([0.0, 0.0, 0.0, 1.0]) == "neutral"


# ---------------------------------------------------------------------------
# PostureState — update rules and Health-bucket derivation
# ---------------------------------------------------------------------------
class TestPostureUpdate:
    """update_posture is the only writer of company.posture in v1.

    Two axes move independently:
      - momentum: fast EMA, half-life ~2 weeks
      - risk_appetite: slow accumulator, asymmetric (wipes 4× sweeps)
    """

    def test_empty_memory_is_noop(self):
        """update_posture with no recorded snapshots must not mutate posture."""
        p = PostureState(momentum=0.3, risk_appetite=0.5, weeks_observed=4)
        mem = CompanyMemory()
        update_posture(p, mem)
        assert p.momentum == pytest.approx(0.3)
        assert p.risk_appetite == pytest.approx(0.5)
        assert p.weeks_observed == 4

    def test_neutral_week_barely_drifts(self):
        """A break-even week (mixed returns at baseline) shouldn't push
        either axis past a small amount."""
        p = PostureState()
        mem = CompanyMemory()
        _record_and_update(p, mem, price_change_pct=0.0, squads_deployed=3,
                           squads_returned=2, squads_eliminated=1)
        # With 1 snapshot in memory: avg = this week's value, identical to old behaviour.
        # return_rate = (2-1)/3 = 0.333; this_week = 0.5*0 + 0.5*0.333 = 0.167
        # momentum = 0.7*0 + 0.3*0.167 = 0.05
        assert abs(p.momentum) < 0.10
        # Mixed week with return_rate > 0 → +0.02 risk drift
        assert p.risk_appetite == pytest.approx(RISK_APPETITE_STEP)
        assert p.weeks_observed == 1

    def test_total_wipe_pushes_risk_appetite_negative_by_penalty(self):
        """All squads eliminated → exact −WIPE_PENALTY to risk_appetite."""
        p = PostureState()
        mem = CompanyMemory()
        _record_and_update(p, mem, price_change_pct=-10.0, squads_deployed=3,
                           squads_returned=0, squads_eliminated=3)
        assert p.risk_appetite == pytest.approx(-RISK_APPETITE_WIPE_PENALTY)
        assert p.momentum < 0   # also bad momentum

    def test_clean_sweep_pushes_risk_appetite_positive_by_bonus(self):
        """All squads returned, none eliminated → exact +SWEEP_BONUS."""
        p = PostureState()
        mem = CompanyMemory()
        _record_and_update(p, mem, price_change_pct=+10.0, squads_deployed=3,
                           squads_returned=3, squads_eliminated=0)
        assert p.risk_appetite == pytest.approx(RISK_APPETITE_SWEEP_BONUS)
        assert p.momentum > 0

    def test_momentum_ema_approaches_one_under_uniform_signal(self):
        """Applying a strong positive signal repeatedly drives momentum
        toward +1. With identical snapshots the windowed mean equals the
        per-week value, so this matches the pre-memory convergence pace."""
        p = PostureState()
        mem = CompanyMemory()
        for _ in range(5):
            _record_and_update(p, mem, price_change_pct=+50.0, squads_deployed=3,
                               squads_returned=3, squads_eliminated=0)
        assert p.momentum > 0.7

    def test_momentum_decay_under_mixed_history(self):
        """The 6-week window smooths the per-week input — momentum decays
        more slowly than the bare-EMA model. After 5 hot weeks then 5
        neutral, the window still carries some warmth so momentum should
        decay BUT not all the way back to zero in just 5 weeks."""
        p = PostureState()
        mem = CompanyMemory()
        for _ in range(5):
            _record_and_update(p, mem, price_change_pct=+50.0, squads_deployed=3,
                               squads_returned=3, squads_eliminated=0)
        peak_momentum = p.momentum
        for _ in range(5):
            _record_and_update(p, mem, price_change_pct=0.0, squads_deployed=3,
                               squads_returned=2, squads_eliminated=1)
        # Decay HAS happened (window is now mostly neutral after 5 weeks).
        assert p.momentum < peak_momentum
        # But residual warmth from the windowed mean keeps it above neutral.
        assert p.momentum > 0.0

    def test_risk_appetite_clamped_at_negative_one(self):
        """Many wipes shouldn't push risk_appetite below −1.0."""
        p = PostureState()
        mem = CompanyMemory()
        for _ in range(100):
            _record_and_update(p, mem, price_change_pct=-10.0, squads_deployed=3,
                               squads_returned=0, squads_eliminated=3)
        assert p.risk_appetite == pytest.approx(-1.0)

    def test_risk_appetite_clamped_at_positive_one(self):
        """Many sweeps shouldn't push risk_appetite above +1.0."""
        p = PostureState()
        mem = CompanyMemory()
        for _ in range(100):
            _record_and_update(p, mem, price_change_pct=+10.0, squads_deployed=3,
                               squads_returned=3, squads_eliminated=0)
        assert p.risk_appetite == pytest.approx(1.0)

    def test_sitting_out_week_does_not_drift_risk_appetite(self):
        """squads_deployed=0 means no signal — risk_appetite stays put."""
        p = PostureState(risk_appetite=+0.5)
        mem = CompanyMemory()
        _record_and_update(p, mem, price_change_pct=0.0, squads_deployed=0,
                           squads_returned=0, squads_eliminated=0)
        assert p.risk_appetite == pytest.approx(0.5)
        # Momentum still drifts toward 0 (price_signal=0, return_rate=0)
        assert p.weeks_observed == 1


class TestPostureToHealth:
    """The legacy Health enum is derived from continuous posture for display."""

    def test_thriving_when_score_above_threshold(self):
        assert posture_to_health(PostureState(momentum=0.4, risk_appetite=0.2)) == "thriving"

    def test_struggling_when_score_below_threshold(self):
        assert posture_to_health(PostureState(momentum=-0.4, risk_appetite=-0.2)) == "struggling"

    def test_neutral_inside_band(self):
        assert posture_to_health(PostureState(momentum=0.1, risk_appetite=0.1)) == "neutral"

    def test_neutral_at_default(self):
        assert posture_to_health(PostureState()) == "neutral"

    def test_mixed_axes_average(self):
        """Score is (momentum + risk)/2. High mood + bad disposition averages neutral."""
        assert posture_to_health(PostureState(momentum=0.8, risk_appetite=-0.5)) == "neutral"


class TestPostureDecisions:
    """Validate that decision functions respond continuously to posture."""

    def test_drops_count_increases_as_risk_appetite_decreases(self):
        """As risk_appetite drops from -0.2 to -1.0, the drop threshold shrinks
        from 2.2×median to 1.0×median, so more runners get cut.

        Runners are spaced to fall into the threshold range at progressive
        risk levels (median ≈ 50):
          risk=-0.2 → threshold≈110 → drops huge (215) and big (105 ≤ 110, kept)
                                       wait: 105 < 110 so kept → drops huge only
          risk=-0.6 → threshold≈80  → drops huge, big (105>80), mid_high (75 kept!)
          risk=-1.0 → threshold≈50  → drops huge, big, mid_high, mid (50, kept)
        """
        from copy import deepcopy
        cheap   = _make_runner(runner_id=1, net_loot=0)            # ~15
        mid     = _make_runner(runner_id=2, net_loot=2000)         # ~45
        mid_hi  = _make_runner(runner_id=3, net_loot=2000, eliminations=15)  # ~67
        big     = _make_runner(runner_id=4, net_loot=5000, eliminations=10)  # ~105
        huge    = _make_runner(runner_id=5, eliminations=50, deployments_survived=50)  # ~215
        original = [cheap, mid, mid_hi, big, huge]
        for r in original:
            refresh_upkeep(r)

        company = FakeCompany("TestCo")
        # Run at three different risk levels with a non-trivial neg momentum
        # so the skip-guard doesn't fire.
        counts = []
        for risk in (-0.2, -0.6, -1.0):
            roster = _populate_roster([deepcopy(r) for r in original])
            for r in roster.runners:
                refresh_upkeep(r)
            drops = decide_voluntary_drops(
                company, roster, PostureState(momentum=-0.5, risk_appetite=risk)
            )
            counts.append(len(drops))
        # Monotone non-decreasing as posture gets more conservative
        assert counts[0] <= counts[1] <= counts[2]
        # At least one drop case differs (sanity)
        assert counts[2] > counts[0]

    def test_bid_amount_scales_with_combined_posture(self):
        """spend_multiplier = 1 + 0.5×max(risk+mood, 0). At (+1,+1) the bid
        should be ~2× the bid at (0,0)."""
        company = FakeCompany("TestCo", budget=2000.0)
        roster = _populate_roster([_make_runner(runner_id=99, net_loot=2000)])
        refresh_upkeep(roster.runners[0])

        candidate = _make_runner(runner_id=1, affinities={"Destroyer": 0.5})
        refresh_upkeep(candidate)

        bids_neutral = decide_acquisitions(
            company, roster, [candidate], PostureState(0.0, 0.0), max_slots=1
        )
        bids_aggressive = decide_acquisitions(
            company, roster, [candidate], PostureState(1.0, 1.0), max_slots=1
        )
        # 1.0 vs 2.0 spend_multiplier → exactly 2× ratio
        assert bids_aggressive[0][1] == pytest.approx(2.0 * bids_neutral[0][1])


# ---------------------------------------------------------------------------
# decide_voluntary_drops — posture-driven dumps
# ---------------------------------------------------------------------------
# Updated for the continuous PostureState model. The old discrete Health
# enum is still tested via TestCompanyHealth + TestPostureToHealth.
_THRIVING = PostureState(momentum=0.5, risk_appetite=0.5)
_NEUTRAL  = PostureState(momentum=0.0, risk_appetite=0.0)
_STRUGGLING = PostureState(momentum=-0.5, risk_appetite=-0.5)


class TestDecideVoluntaryDrops:
    def test_thriving_drops_no_one(self):
        """Thriving posture skips the drop pass — momentum > -0.1, risk > -0.1."""
        company = FakeCompany("TestCo")
        runners = [_make_runner(runner_id=i, net_loot=i*1000) for i in range(1, 6)]
        for r in runners:
            refresh_upkeep(r)
        roster = _populate_roster(runners)
        assert decide_voluntary_drops(company, roster, _THRIVING) == []

    def test_neutral_drops_no_one(self):
        """Neutral posture also skips — both axes at 0.0 satisfy the > -0.1 guard."""
        company = FakeCompany("TestCo")
        runners = [_make_runner(runner_id=i, net_loot=i*1000) for i in range(1, 6)]
        for r in runners:
            refresh_upkeep(r)
        roster = _populate_roster(runners)
        assert decide_voluntary_drops(company, roster, _NEUTRAL) == []

    def test_struggling_drops_above_threshold(self):
        """Struggling posture (-0.5, -0.5) → threshold = 2.5 + 1.5×(-0.5) = 1.75× median.
        With median ~45 and expensive ~215, expensive (>78.75) gets dropped."""
        company = FakeCompany("TestCo")
        cheap     = _make_runner(runner_id=1, net_loot=0)                   # ~15
        median    = _make_runner(runner_id=2, net_loot=2000)                # ~45
        expensive = _make_runner(runner_id=3, eliminations=50,
                                 deployments_survived=50)                   # ~215
        for r in (cheap, median, expensive):
            refresh_upkeep(r)
        roster = _populate_roster([cheap, median, expensive])

        drops = decide_voluntary_drops(company, roster, _STRUGGLING)
        assert [r.id for r in drops] == [3]
        assert expensive not in roster.runners


# ---------------------------------------------------------------------------
# decide_acquisitions — posture-driven preference & bid
# ---------------------------------------------------------------------------
class TestDecideAcquisitions:
    def test_struggling_picks_cheap_and_safe(self):
        """Struggling posture: low upkeep cap, prefers safe-shell affinity."""
        company = FakeCompany("TestCo", budget=500.0)
        # roster has one mid-cost runner so the median is meaningful
        anchor = _make_runner(runner_id=99, net_loot=2000)
        refresh_upkeep(anchor)
        roster = _populate_roster([anchor])

        cheap_safe  = _make_runner(runner_id=1, affinities={"Recon": 0.7})
        cheap_combat = _make_runner(runner_id=2, affinities={"Destroyer": 0.7})
        expensive   = _make_runner(runner_id=3, eliminations=50, deployments_survived=50,
                                   affinities={"Recon": 0.9})  # over the cap

        bids = decide_acquisitions(
            company, roster, [cheap_safe, cheap_combat, expensive],
            _STRUGGLING, max_slots=2,
        )
        bid_ids = [r.id for (r, _) in bids]
        # Expensive runner exceeds the cap → never appears.
        assert 3 not in bid_ids
        # Safe affinity preferred over aggressive when both are cheap.
        assert bid_ids[0] == 1

    def test_thriving_picks_high_affinity_and_bids_higher(self):
        """Thriving posture: high upkeep cap, prefers aggressive affinity, 1.5× bid."""
        company = FakeCompany("TestCo", budget=2000.0)
        roster = _populate_roster([])

        rookie = _make_runner(runner_id=1)
        veteran = _make_runner(runner_id=2, deployments_survived=20,
                               affinities={"Destroyer": 0.7})

        bids = decide_acquisitions(
            company, roster, [rookie, veteran], _THRIVING, max_slots=2,
        )
        # Thriving company prioritizes the high-affinity veteran.
        assert bids[0][0].id == 2
        # Bid amount: spend_multiplier = 1.0 + 0.5×max(0.5+0.5, 0) = 1.5
        assert bids[0][1] == pytest.approx(1.5 * veteran.upkeep_cost)

    def test_neutral_picks_cheapest(self):
        """Neutral posture with empty roster: no cap → just take the two cheapest."""
        company = FakeCompany("TestCo", budget=500.0)
        roster = _populate_roster([])
        a = _make_runner(runner_id=1, net_loot=5000)   # expensive
        b = _make_runner(runner_id=2)                  # cheap
        c = _make_runner(runner_id=3, net_loot=2000)   # mid
        bids = decide_acquisitions(company, roster, [a, b, c], _NEUTRAL, max_slots=2)
        assert [r.id for (r, _) in bids] == [2, 3]

    def test_no_slots_no_bids(self):
        company = FakeCompany("TestCo", budget=500.0)
        roster = _populate_roster([])
        assert decide_acquisitions(company, roster, [_make_runner()], _NEUTRAL, 0) == []


# ---------------------------------------------------------------------------
# resolve_bidding — sequential draft with contention
# ---------------------------------------------------------------------------
class TestResolveBidding:
    def test_higher_bid_wins_contention(self):
        """Two companies want the same runner. Whichever fires first under the
        shuffled order pays their bid. Subsequent companies fall to runner-up."""
        co_a = FakeCompany("A", budget=1000.0)
        co_b = FakeCompany("B", budget=1000.0)
        rosters = {"A": _populate_roster([]), "B": _populate_roster([])}
        target = _make_runner(runner_id=1, net_loot=3000)
        spare  = _make_runner(runner_id=2)
        for r in (target, spare):
            refresh_upkeep(r)
        free_agents = [target, spare]

        bids = {
            "A": [(target, target.upkeep_cost), (spare, spare.upkeep_cost)],
            "B": [(target, target.upkeep_cost * 1.5), (spare, spare.upkeep_cost)],
        }
        # Force deterministic shuffle order
        rng = random.Random(0)
        signed = resolve_bidding(
            [co_a, co_b], rosters, free_agents, bids, rng, target_roster_size=9,
        )

        all_signed_ids = {r.id for signs in signed.values() for r in signs}
        assert all_signed_ids == {1, 2}
        # Exactly one company got the target; both signed someone.
        assert free_agents == []

    def test_runner_company_name_updated_on_signing(self):
        co = FakeCompany("Alpha", budget=200.0)
        rosters = {"Alpha": _populate_roster([])}
        r = _make_runner(runner_id=1, company_name="(orphan)")
        refresh_upkeep(r)
        bids = {"Alpha": [(r, r.upkeep_cost)]}
        free_agents = [r]
        resolve_bidding([co], rosters, free_agents, bids, random.Random(0), 9)
        assert r.company_name == "Alpha"
        assert r in rosters["Alpha"].runners

    def test_unaffordable_company_skips_all_picks(self):
        co = FakeCompany("Poor", budget=5.0)
        rosters = {"Poor": _populate_roster([])}
        target = _make_runner(runner_id=1, net_loot=5000)
        refresh_upkeep(target)
        free_agents = [target]
        bids = {"Poor": [(target, target.upkeep_cost)]}
        resolve_bidding([co], rosters, free_agents, bids, random.Random(0), 9)
        assert target in free_agents  # nobody could afford them
        assert rosters["Poor"].runners == []


# ---------------------------------------------------------------------------
# tick_free_agent_pool — retirement + rookie spawn floor
# ---------------------------------------------------------------------------
class TestTickFreeAgentPool:
    def test_retires_runners_past_threshold(self):
        market = make_initial_market()
        used = set()
        id_supplier = RunnerIdCounter(next_id=100)
        # Age one runner just over the threshold; another well under.
        old = _make_runner(runner_id=1)
        old.weeks_orphaned = ORPHAN_RETIRE_AFTER_WEEKS  # next tick pushes it over
        young = _make_runner(runner_id=2)
        free_agents = [old, young]

        retired, _spawned = tick_free_agent_pool(
            free_agents, total_employed=MIN_GLOBAL_POOL,  # already above floor
            market=market, used_names=used, id_supplier=id_supplier,
        )
        assert [r.id for r in retired] == [1]
        assert young in free_agents
        assert old not in free_agents

    def test_spawns_rookies_to_meet_minimum_pool(self):
        """If total_employed + len(free_agents) < MIN_GLOBAL_POOL, spawn rookies
        into the pool until the floor is met."""
        market = make_initial_market()
        used = set()
        id_supplier = RunnerIdCounter(next_id=500)
        free_agents = []

        retired, spawned = tick_free_agent_pool(
            free_agents, total_employed=20,  # 20 + 0 < MIN_GLOBAL_POOL=42
            market=market, used_names=used, id_supplier=id_supplier,
        )
        assert retired == []
        assert len(spawned) == MIN_GLOBAL_POOL - 20
        assert len(free_agents) == len(spawned)
        # Each rookie is a real Runner with a fresh id from the supplier.
        for r in spawned:
            assert isinstance(r, Runner)
            assert r.id >= 500

    def test_no_spawn_when_pool_at_floor(self):
        market = make_initial_market()
        used = set()
        id_supplier = RunnerIdCounter(next_id=500)
        free_agents = [_make_runner(runner_id=i) for i in range(10)]

        _retired, spawned = tick_free_agent_pool(
            free_agents,
            total_employed=MIN_GLOBAL_POOL - len(free_agents),  # exactly at floor
            market=market, used_names=used, id_supplier=id_supplier,
        )
        assert spawned == []


# ---------------------------------------------------------------------------
# release_to_free_agents — preserves career stats, resets transient state
# ---------------------------------------------------------------------------
class TestReleaseToFreeAgents:
    def test_preserves_career_stats(self):
        pool = []
        r = _make_runner(runner_id=1, net_loot=2000, eliminations=8,
                         deployments_survived=10,
                         affinities={"Destroyer": 0.6})
        release_to_free_agents(r, pool)
        assert r in pool
        assert r.net_loot == 2000
        assert r.eliminations == 8
        assert r.deployments_survived == 10
        assert r.shell_affinities["Destroyer"] == pytest.approx(0.6)

    def test_resets_transient_state(self):
        pool = []
        r = _make_runner(runner_id=1)
        r.current_shell = "Destroyer"
        r._died_this_week = True
        release_to_free_agents(r, pool)
        assert r.current_shell == ""
        assert not hasattr(r, "_died_this_week")
        assert r.weeks_orphaned == 0


# ---------------------------------------------------------------------------
# collect_company_income — credit-share routing
# ---------------------------------------------------------------------------
class TestCollectCompanyIncome:
    def test_credits_company_share_of_runner_earnings(self):
        co = FakeCompany("X", budget=0.0)
        r1 = _make_runner(runner_id=1)
        r2 = _make_runner(runner_id=2)
        roster = _populate_roster([r1, r2])
        credits_by_id = {1: 100.0, 2: 200.0}
        share = collect_company_income(co, roster, credits_by_id)
        assert share == pytest.approx(300.0 * CREDIT_SHARE_TO_COMPANY)
        assert co.budget == pytest.approx(300.0 * CREDIT_SHARE_TO_COMPANY)

    def test_ignores_runners_not_on_roster(self):
        """A runner who earned credits before being orphaned mid-week shouldn't
        contribute to a former employer's income."""
        co = FakeCompany("X", budget=0.0)
        r1 = _make_runner(runner_id=1)
        roster = _populate_roster([r1])
        credits_by_id = {1: 100.0, 999: 5000.0}   # 999 isn't on this roster
        collect_company_income(co, roster, credits_by_id)
        assert co.budget == pytest.approx(100.0 * CREDIT_SHARE_TO_COMPANY)


# ---------------------------------------------------------------------------
# Loans — take, repay, overdue, cap
# ---------------------------------------------------------------------------
class TestLoans:
    """Loans are the recovery mechanism for companies sliding into the
    death spiral. They inject LOAN_AMOUNT cr but must be repaid within
    LOAN_TERM_WEEKS or accrue a compounding valuation penalty."""

    def test_loan_taken_when_broke_and_undersized(self):
        """Conditions: budget < threshold AND roster < min for deployment."""
        co = FakeCompany("X", budget=200.0)   # below LOAN_TRIGGER_BUDGET_THRESHOLD (500)
        roster = _populate_roster([_make_runner(runner_id=i) for i in range(5)])  # 5 < 6
        loan = take_loan_if_needed(co, roster, current_week=3)
        assert loan is not None
        assert loan.amount == LOAN_AMOUNT
        assert loan.week_taken == 3
        assert loan.repaid is False
        assert co.budget == pytest.approx(200.0 + LOAN_AMOUNT)
        assert co.loans == [loan]

    def test_no_loan_when_budget_above_threshold(self):
        """Healthy budget → no loan, even if roster is short."""
        co = FakeCompany("X", budget=2000.0)  # well above threshold
        roster = _populate_roster([_make_runner(runner_id=i) for i in range(5)])
        loan = take_loan_if_needed(co, roster, current_week=3)
        assert loan is None
        assert co.budget == 2000.0
        assert co.loans == []

    def test_no_loan_when_roster_can_deploy(self):
        """A 6-runner roster CAN deploy — no rescue needed."""
        co = FakeCompany("X", budget=100.0)
        roster = _populate_roster([_make_runner(runner_id=i) for i in range(6)])
        loan = take_loan_if_needed(co, roster, current_week=3)
        assert loan is None
        assert co.loans == []

    def test_loan_cap_blocks_further_borrowing(self):
        """Stacked loans hit MAX_OUTSTANDING_LOANS and can't borrow more."""
        co = FakeCompany("X", budget=100.0)
        for w in range(MAX_OUTSTANDING_LOANS):
            co.loans.append(Loan(week_taken=w))
        roster = _populate_roster([_make_runner(runner_id=i) for i in range(3)])
        assert take_loan_if_needed(co, roster, current_week=10) is None

    def test_repaid_loans_dont_count_toward_cap(self):
        """If older loans were repaid, the cap doesn't block new borrowing."""
        co = FakeCompany("X", budget=100.0)
        for w in range(MAX_OUTSTANDING_LOANS):
            l = Loan(week_taken=w)
            l.repaid = True
            l.week_repaid = w + 4
            co.loans.append(l)
        roster = _populate_roster([_make_runner(runner_id=i) for i in range(3)])
        new_loan = take_loan_if_needed(co, roster, current_week=20)
        assert new_loan is not None

    def test_auto_repay_when_budget_high(self):
        """Once budget exceeds LOAN_REPAY_BUDGET_THRESHOLD, oldest loan repays."""
        co = FakeCompany("X", budget=4000.0)  # above threshold
        co.loans = [
            Loan(week_taken=2),   # oldest — should repay first
            Loan(week_taken=5),
        ]
        repaid = auto_repay_loan(co, current_week=10)
        assert repaid is not None
        assert repaid.week_taken == 2
        assert repaid.repaid is True
        assert repaid.week_repaid == 10
        assert co.budget == pytest.approx(4000.0 - LOAN_AMOUNT)

    def test_no_repay_when_budget_below_threshold(self):
        co = FakeCompany("X", budget=500.0)  # below threshold
        co.loans = [Loan(week_taken=2)]
        assert auto_repay_loan(co, current_week=10) is None
        assert co.budget == 500.0
        assert co.loans[0].repaid is False

    def test_no_repay_when_no_outstanding_loans(self):
        co = FakeCompany("X", budget=5000.0)
        co.loans = []
        assert auto_repay_loan(co, current_week=10) is None

    def test_overdue_loans_filter(self):
        """A loan past LOAN_TERM_WEEKS old without repayment is overdue."""
        loans = [
            Loan(week_taken=1),                          # >= 12 weeks old → overdue
            Loan(week_taken=10, repaid=True),            # repaid → not overdue
            Loan(week_taken=20),                         # only 3 weeks old → not overdue
        ]
        overdue = overdue_loans(loans, current_week=23)
        assert len(overdue) == 1
        assert overdue[0].week_taken == 1

    def test_overdue_exact_term_boundary(self):
        """A loan exactly LOAN_TERM_WEEKS old IS overdue (>= boundary)."""
        loans = [Loan(week_taken=0)]
        assert len(overdue_loans(loans, current_week=LOAN_TERM_WEEKS)) == 1

    def test_outstanding_filter_excludes_repaid(self):
        loans = [
            Loan(week_taken=1),
            Loan(week_taken=2, repaid=True),
            Loan(week_taken=3),
        ]
        assert len(outstanding_loans(loans)) == 2
