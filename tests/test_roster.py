"""
Tests for runner_sim/market/roster.py

Key invariants:
  - Simplex triple always sums to 1.0 and all components are non-negative
  - cull_dead_runners removes only flagged runners and accumulates total_deaths
  - No runner ID appears twice in a roster after creation
"""

import pytest

from runner_sim.market.roster import (
    STARTING_ROSTER_SIZE,
    CompanyRoster,
    _random_simplex_triple,
    create_roster,
    cull_dead_runners,
)
from runner_sim.market.shell_market import make_initial_market


# ---------------------------------------------------------------------------
# _random_simplex_triple
# ---------------------------------------------------------------------------
class TestRandomSimplexTriple:
    def test_sums_to_one(self):
        """Every sample must lie exactly on the 2-simplex."""
        for _ in range(500):
            a, b, c = _random_simplex_triple()
            assert abs(a + b + c - 1.0) < 1e-9, f"Got ({a}, {b}, {c}) sum={a+b+c}"

    def test_all_components_non_negative(self):
        """No component should be negative — all attributes start at ≥ 0."""
        for _ in range(500):
            a, b, c = _random_simplex_triple()
            assert a >= 0 and b >= 0 and c >= 0

    def test_produces_variety(self):
        """Consecutive calls should not all return the same value."""
        results = {_random_simplex_triple() for _ in range(20)}
        assert len(results) > 5, "Expected varied simplex samples, got near-identical results"


# ---------------------------------------------------------------------------
# create_roster
# ---------------------------------------------------------------------------
class TestCreateRoster:
    def test_creates_correct_number_of_runners(self):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        assert len(roster.runners) == STARTING_ROSTER_SIZE

    def test_all_runner_ids_unique(self):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        ids = [r.id for r in roster.runners]
        assert len(ids) == len(set(ids))

    def test_all_runners_have_a_shell(self):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        for runner in roster.runners:
            assert runner.current_shell != ""

    def test_attributes_sum_to_one(self):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        for r in roster.runners:
            total = r.combat + r.extraction + r.support
            assert abs(total - 1.0) < 1e-9, f"{r.name}: attrs sum to {total}"


# ---------------------------------------------------------------------------
# cull_dead_runners / total_deaths
# ---------------------------------------------------------------------------
# The v0 `replace_dead_runners` auto-hired fresh recruits whenever runners
# died. v1 splits responsibilities: `cull_dead_runners` removes flagged
# runners and accumulates `total_deaths`; refilling rosters is the job of
# the company-AI bidding flow in week.py + company_strategy.py.
class TestCullDeadRunners:
    def _roster_with_deaths(self, n_dead: int) -> tuple[CompanyRoster, set[str]]:
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        for runner in roster.runners[:n_dead]:
            runner._died_this_week = True
            runner.death_count += 1
        return roster, used

    def test_dead_runners_removed_from_roster(self):
        roster, _ = self._roster_with_deaths(3)
        cull_dead_runners(roster)
        assert len(roster.runners) == STARTING_ROSTER_SIZE - 3
        assert all(not getattr(r, "_died_this_week", False) for r in roster.runners)

    def test_returns_the_dead_runners(self):
        roster, _ = self._roster_with_deaths(2)
        dead = cull_dead_runners(roster)
        assert len(dead) == 2
        for r in dead:
            assert getattr(r, "_died_this_week", False) is True

    def test_total_deaths_accumulates(self):
        roster, _ = self._roster_with_deaths(3)
        cull_dead_runners(roster)
        assert roster.total_deaths == 3

        for runner in roster.runners[:2]:
            runner._died_this_week = True
            runner.death_count += 1
        cull_dead_runners(roster)
        assert roster.total_deaths == 5

    def test_zero_deaths_leaves_roster_unchanged(self):
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        ids_before = [r.id for r in roster.runners]
        dead = cull_dead_runners(roster)
        assert dead == []
        assert [r.id for r in roster.runners] == ids_before
        assert roster.total_deaths == 0
