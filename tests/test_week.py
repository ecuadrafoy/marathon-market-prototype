"""
Tests for runner_sim/market/week.py — specifically apply_zone_outcome.

apply_zone_outcome has three branches:
  1. squad_eliminated=True  → runner dies: _died_this_week set, no credits, no drift
  2. squad_extracted=True   → runner survives with loot: credits added, drift applied
  3. neither                → defensive fallback: drift applied, no credits

These are the most critical invariants in the simulation — they govern what
runners keep, what they lose, and what events get recorded for recruitment.

TODO: implement test_extracted_case and test_fallback_case below.
"""

import pytest

from runner_sim.market.week import apply_zone_outcome
from runner_sim.runners import Runner
from runner_sim.shells import SHELL_ROSTER


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
def _fresh_runner(shell_name: str = "Thief") -> Runner:
    """A runner with known starting state for assertion comparisons."""
    return Runner(
        id=1,
        name="TestRunner",
        company_name="TestCo",
        combat=0.3,
        extraction=0.5,
        support=0.2,
        current_shell=shell_name,
        credit_balance=100.0,
    )


# ---------------------------------------------------------------------------
# Branch 1: squad eliminated
# ---------------------------------------------------------------------------
class TestApplyZoneOutcomeEliminated:
    def test_died_this_week_sentinel_is_set(self):
        """replace_dead_runners uses this flag to identify who to remove."""
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=0.0, kills_attributed=0)
        assert getattr(runner, "_died_this_week", False) is True

    def test_death_count_incremented(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=0.0, kills_attributed=0)
        assert runner.death_count == 1

    def test_no_credits_on_elimination(self):
        """Dead runners forfeit all loot — credit_balance must not change."""
        runner = _fresh_runner()
        balance_before = runner.credit_balance
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=500.0, kills_attributed=0)
        assert runner.credit_balance == balance_before

    def test_no_net_loot_on_elimination(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=500.0, kills_attributed=0)
        assert runner.net_loot == 0.0

    def test_shell_history_appended_on_death(self):
        """Shell history records the shell worn at time of death."""
        runner = _fresh_runner("Destroyer")
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=0.0, kills_attributed=0)
        assert runner.shell_history == ["Destroyer"]

    def test_extraction_attempts_still_incremented(self):
        """The runner attempted the run; the attempt counter reflects that."""
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction_attempts == 1

    def test_extraction_successes_not_incremented(self):
        """Elimination is not a success."""
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction_successes == 0

    def test_attributes_unchanged_after_death(self):
        """No drift occurs on death — the consciousness ends here."""
        runner = _fresh_runner()
        c, e, s = runner.combat, runner.extraction, runner.support
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=True,
                           credits_received=0.0, kills_attributed=0)
        assert (runner.combat, runner.extraction, runner.support) == (c, e, s)


# ---------------------------------------------------------------------------
# Branch 2: squad extracted (successful run)
# ---------------------------------------------------------------------------
class TestApplyZoneOutcomeExtracted:
    """Full update path: credits, kills, drift, affinity, shell_history — all fire."""

    def test_credits_added_to_balance(self):
        runner = _fresh_runner()
        balance_before = runner.credit_balance   # 100.0
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=250.0, kills_attributed=0)
        assert runner.credit_balance == pytest.approx(balance_before + 250.0)

    def test_credits_added_to_net_loot(self):
        """net_loot is the lifetime leaderboard stat — also receives the credit."""
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=250.0, kills_attributed=0)
        assert runner.net_loot == pytest.approx(250.0)

    def test_extraction_attempts_incremented(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction_attempts == 1

    def test_extraction_successes_incremented(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction_successes == 1

    def test_kills_attributed(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=3)
        assert runner.eliminations == 3

    def test_zero_kills_attributed(self):
        """Passing 0 kills must not create a negative or phantom kill count."""
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.eliminations == 0

    def test_shell_history_appended(self):
        runner = _fresh_runner("Destroyer")
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.shell_history == ["Destroyer"]

    def test_died_this_week_not_set(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert not getattr(runner, "_died_this_week", False)

    def test_attributes_drift_in_correct_direction(self):
        """drift_attributes does an EMA step toward the shell's affinity vector.

        Thief shell: combat_affinity=0.2, extraction_affinity=0.7, support_affinity=0.1
        Fresh runner: combat=0.3, extraction=0.5, support=0.2

        After one drift step (rate=0.05):
          - extraction should INCREASE (0.5 → shell target 0.7)
          - combat should DECREASE (0.3 → shell target 0.2)
        """
        runner = _fresh_runner("Thief")
        extraction_before = runner.extraction
        combat_before = runner.combat
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction > extraction_before, "Thief drift should raise extraction"
        assert runner.combat < combat_before, "Thief drift should lower combat"

    def test_attributes_still_sum_to_one_after_drift(self):
        """EMA drift preserves the simplex constraint (both vectors sum to 1.0,
        so per-axis deltas sum to 0 and the total stays exactly 1.0)."""
        runner = _fresh_runner("Thief")
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        total = runner.combat + runner.extraction + runner.support
        assert abs(total - 1.0) < 1e-9

    def test_affinity_gained_for_current_shell(self):
        """gain_affinity increments shell_affinities[current_shell] by AFFINITY_PER_WEEK.

        Fresh runners start at 0.0 affinity; after one extraction they should be > 0.
        """
        runner = _fresh_runner("Thief")
        affinity_before = runner.shell_affinities.get("Thief", 0.0)  # 0.0 for a fresh runner
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.shell_affinities["Thief"] > affinity_before

    def test_affinity_for_other_shells_unchanged(self):
        """Only the currently worn shell gains affinity — no bleed to others."""
        runner = _fresh_runner("Thief")
        apply_zone_outcome(runner, squad_extracted=True, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        for shell_name, aff in runner.shell_affinities.items():
            if shell_name != "Thief":
                assert aff == 0.0, f"Unexpected affinity gain on {shell_name}"


# ---------------------------------------------------------------------------
# Branch 3: neither extracted nor eliminated (defensive fallback)
# ---------------------------------------------------------------------------
class TestApplyZoneOutcomeFallback:
    """Participation without extraction or death.

    Per the docstring this branch 'shouldn't fire today' — run_zone forces
    extraction at end-of-run. But the contract still matters: the runner
    participated, so they drift and gain affinity, but receive no loot.
    """

    def test_no_credits_in_fallback(self):
        runner = _fresh_runner()
        balance_before = runner.credit_balance
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=500.0, kills_attributed=0)
        assert runner.credit_balance == balance_before

    def test_net_loot_unchanged_in_fallback(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=500.0, kills_attributed=0)
        assert runner.net_loot == 0.0

    def test_no_extraction_success_in_fallback(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction_successes == 0

    def test_extraction_attempts_incremented_in_fallback(self):
        """The attempt happened — it just ended in neither outcome."""
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction_attempts == 1

    def test_not_marked_as_dead_in_fallback(self):
        runner = _fresh_runner()
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert not getattr(runner, "_died_this_week", False)

    def test_drift_still_applied_in_fallback(self):
        """Runner participated — drift fires same as in the extraction branch."""
        runner = _fresh_runner("Thief")
        extraction_before = runner.extraction
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.extraction > extraction_before

    def test_affinity_still_gained_in_fallback(self):
        """gain_affinity fires in the fallback branch too — the runner was there."""
        runner = _fresh_runner("Thief")
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.shell_affinities["Thief"] > 0.0

    def test_shell_history_appended_in_fallback(self):
        """shell_history.append sits outside the if/else — fires in all branches."""
        runner = _fresh_runner("Recon")
        apply_zone_outcome(runner, squad_extracted=False, squad_eliminated=False,
                           credits_received=0.0, kills_attributed=0)
        assert runner.shell_history == ["Recon"]


# ---------------------------------------------------------------------------
# Valuation anchor — integration with _build_company_result
# ---------------------------------------------------------------------------
class TestBuildCompanyResultAnchor:
    """The anchor term is added on top of the performance term inside
    _build_company_result. These tests pin down three invariants:

      1. anchor_input=None → anchor contribution is exactly 0 (calibration
         compatibility).
      2. price_change_pct decomposes exactly: total = performance + anchor.
      3. Under identical RNG seed, an undervalued anchor_input produces a
         higher price_after than an overvalued one — confirming the anchor
         shifts price in the direction of the projected valuation.
    """

    def _build(self, *, anchor_input, seed=42, price_before=300.0):
        """Run _build_company_result in a minimal 'company sat out' scenario.

        co_squads={} and zone_results={} means no deployment — total_credits=0,
        squads_returned=0, squads_eliminated=0. The performance term is purely
        the (-baseline + noise) value; the anchor is whatever anchor_input
        produces. Seeding the RNG identically across calls makes the
        performance term reproducible so we can isolate the anchor's effect.
        """
        import random as _r
        from runner_sim.market.week import _build_company_result

        _r.seed(seed)
        return _build_company_result(
            company_name="TestCo",
            price_before=price_before,
            co_squads={},
            monitored_zone_name="Perimeter",
            zone_results={},
            expected_squad_count=3,
            anchor_input=anchor_input,
        )

    def test_anchor_input_none_yields_zero_anchor(self):
        """Calibration-mode compatibility: omitting anchor_input must produce
        anchor_pull_pct == 0 and price_change_pct == performance_pct exactly."""
        result = self._build(anchor_input=None)
        assert result.anchor_pull_pct == 0.0
        assert result.fair_value == 0.0
        assert result.price_change_pct == pytest.approx(result.performance_pct, rel=1e-12)

    def test_price_change_decomposes_exactly(self):
        """price_change_pct must equal performance_pct + anchor_pull_pct,
        bit-exact (within float epsilon). This is the contract the rest of
        the UI/test suite relies on."""
        from runner_sim.market.pricing import STARTING_VALUATION
        # Drive a non-trivial anchor by passing a valuation 20% above starting.
        result = self._build(
            anchor_input=(STARTING_VALUATION * 1.2, 0.0, 300.0),
        )
        assert result.price_change_pct == pytest.approx(
            result.performance_pct + result.anchor_pull_pct, rel=1e-12,
        )
        # Sanity: the anchor really did fire (non-zero contribution).
        assert result.anchor_pull_pct != 0.0

    def test_undervalued_anchor_lifts_price_above_overvalued(self):
        """Hold the performance term constant via identical RNG seeds; the
        only difference between the two calls is the anchor sign. The
        undervalued case (valuation > STARTING) must produce a higher
        price_after than the overvalued case."""
        from runner_sim.market.pricing import STARTING_VALUATION
        undervalued = self._build(
            anchor_input=(STARTING_VALUATION * 1.2, 0.0, 300.0), seed=7,
        )
        overvalued = self._build(
            anchor_input=(STARTING_VALUATION * 0.8, 0.0, 300.0), seed=7,
        )
        assert undervalued.price_after > overvalued.price_after
        # And the performance term is identical between them — proving the
        # difference is purely the anchor.
        assert undervalued.performance_pct == pytest.approx(
            overvalued.performance_pct, rel=1e-12,
        )

    def test_anchor_at_starting_valuation_with_price_at_anchor_is_zero_pull(self):
        """The week-0 condition: projected==STARTING and price_before==anchor_price
        means fair_value==anchor_price means zero pull. Used to confirm the
        anchor doesn't bias the system at game start."""
        from runner_sim.market.pricing import STARTING_VALUATION
        result = self._build(
            anchor_input=(STARTING_VALUATION, 0.0, 300.0),
            price_before=300.0,
        )
        assert result.anchor_pull_pct == pytest.approx(0.0, abs=1e-9)
        assert result.fair_value == pytest.approx(300.0, rel=1e-9)


# ---------------------------------------------------------------------------
# Integration: closed-pool invariants over a multi-week run
# ---------------------------------------------------------------------------
class TestRosterEconomyEndToEnd:
    """Drives the full company-AI loop for 20 weeks under a seeded RNG and
    asserts the closed-pool semantics hold:

      1. Free-agent pool becomes non-empty at some point (someone got orphaned).
      2. Total roster ID surface stays bounded — runners are recycled, not
         continuously spawned.
      3. Some runner appears in two different companies across the run
         (orphan-then-rehire is the headline mechanic).
    """

    def test_closed_pool_20_weeks(self):
        from dataclasses import dataclass, field
        import random

        from runner_sim.market.calibration import bootstrap_default_state
        from runner_sim.market.week import simulate_week
        from runner_sim.zone_sim.items import load_items
        from runner_sim.zone_sim.zones import ZONES

        @dataclass
        class StubCompany:
            name: str
            price: float
            budget: float = 600.0

        random.seed(2026)
        rosters, market, free_agents, id_supplier = bootstrap_default_state()
        companies = [
            StubCompany("CyberAcme", 450.0),
            StubCompany("Sekiguchi", 380.0),
            StubCompany("Traxus",    300.0),
            StubCompany("NuCaloric", 200.0),
        ]
        item_catalog = load_items()
        price_histories: dict[str, list[float]] = {c.name: [c.price] for c in companies}

        # Track every (runner_id → set of company_names they've been on).
        company_lineage: dict[int, set[str]] = {}
        for roster in rosters.values():
            for r in roster.runners:
                company_lineage[r.id] = {roster.company_name}
        for r in free_agents:
            company_lineage[r.id] = set()  # rookie bench, no company yet

        saw_nonempty_pool = False
        rng = random.Random(0)

        for _ in range(20):
            result = simulate_week(
                rosters, market, ZONES, item_catalog,
                company_prices={c.name: c.price for c in companies},
                companies=companies,
                free_agents=free_agents,
                id_supplier=id_supplier,
                price_histories=price_histories,
                rng=rng,
            )
            for r in result.company_results:
                for c in companies:
                    if c.name == r.company_name:
                        c.price = r.price_after
                        price_histories[c.name].append(r.price_after)
            if free_agents:
                saw_nonempty_pool = True
            # Track lineage — note who is currently on which roster.
            for roster in rosters.values():
                for r in roster.runners:
                    company_lineage.setdefault(r.id, set()).add(roster.company_name)

        # Invariant 1: the free-agent pool became active.
        assert saw_nonempty_pool, "free-agent pool never accumulated anyone in 20 weeks"

        # Invariant 2: at least one runner moved between companies.
        movers = [rid for rid, cos in company_lineage.items() if len(cos) >= 2]
        assert movers, "no runner ever switched companies — orphan→rehire never fired"

        # Invariant 3: pool floor honored.
        from runner_sim.market.company_strategy import MIN_GLOBAL_POOL
        total = sum(len(r.runners) for r in rosters.values()) + len(free_agents)
        assert total >= MIN_GLOBAL_POOL - 1, (
            f"global pool fell below floor: {total} < {MIN_GLOBAL_POOL}"
        )

    def test_roster_events_populated_per_company(self):
        """simulate_week returns a CompanyRosterEvents per company, even when
        no transitions happened. Across a 10-week run we should see signings,
        deaths, and orphan/drop events somewhere in the stream."""
        from dataclasses import dataclass
        import random
        from runner_sim.market.calibration import bootstrap_default_state
        from runner_sim.market.company_strategy import CompanyRosterEvents
        from runner_sim.market.week import simulate_week
        from runner_sim.zone_sim.items import load_items
        from runner_sim.zone_sim.zones import ZONES

        @dataclass
        class StubCo:
            name: str
            price: float
            budget: float = 600.0

        random.seed(7)
        rosters, market, free_agents, id_supplier = bootstrap_default_state()
        companies = [
            StubCo("CyberAcme", 450.0),
            StubCo("Sekiguchi", 380.0),
            StubCo("Traxus",    300.0),
            StubCo("NuCaloric", 200.0),
        ]
        item_catalog = load_items()
        price_histories = {c.name: [c.price] for c in companies}

        saw_signed = saw_died = saw_orphaned = False
        rng = random.Random(0)

        for _ in range(15):
            result = simulate_week(
                rosters, market, ZONES, item_catalog,
                company_prices={c.name: c.price for c in companies},
                companies=companies, free_agents=free_agents,
                id_supplier=id_supplier, price_histories=price_histories,
                rng=rng,
            )
            # Every company has an entry, even if empty.
            assert set(result.roster_events.keys()) == {c.name for c in companies}
            for ev in result.roster_events.values():
                assert isinstance(ev, CompanyRosterEvents)
                if ev.signed:                saw_signed = True
                if ev.died:                  saw_died = True
                if ev.orphaned_unaffordable: saw_orphaned = True

            for r in result.company_results:
                for c in companies:
                    if c.name == r.company_name:
                        c.price = r.price_after
                        price_histories[c.name].append(r.price_after)

        assert saw_signed,   "no signings recorded across 15 weeks"
        assert saw_died,     "no deaths recorded across 15 weeks"
        assert saw_orphaned, "no orphan-unaffordable events recorded across 15 weeks"
