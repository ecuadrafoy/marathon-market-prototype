"""
Tests for runner_sim/market/roster.py

Key invariants:
  - Simplex triple always sums to 1.0 and all components are non-negative
  - replace_dead_runners restores roster to exactly STARTING_ROSTER_SIZE
  - total_deaths accumulates correctly and is not reset when the runner is removed
  - No runner ID appears twice in a roster after replacements
"""

import pytest

from runner_sim.market.roster import (
    STARTING_ROSTER_SIZE,
    CompanyRoster,
    _random_simplex_triple,
    create_roster,
    replace_dead_runners,
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
# replace_dead_runners / total_deaths
# ---------------------------------------------------------------------------
class TestReplaceDeadRunners:
    def _roster_with_deaths(self, n_dead: int) -> tuple[CompanyRoster, set[str]]:
        """Create a fresh roster and mark n_dead runners as killed this week."""
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        for runner in roster.runners[:n_dead]:
            runner._died_this_week = True
            runner.death_count += 1
        return roster, used

    def test_roster_restored_to_nine_after_one_death(self):
        market = make_initial_market()
        roster, used = self._roster_with_deaths(1)
        replace_dead_runners(roster, market, used)
        assert len(roster.runners) == STARTING_ROSTER_SIZE

    def test_roster_restored_to_nine_after_three_deaths(self):
        """One full squad wipe — the most common loss scenario."""
        market = make_initial_market()
        roster, used = self._roster_with_deaths(3)
        replace_dead_runners(roster, market, used)
        assert len(roster.runners) == STARTING_ROSTER_SIZE

    def test_total_deaths_accumulates(self):
        """total_deaths should count deaths across multiple weeks, not reset."""
        market = make_initial_market()
        roster, used = self._roster_with_deaths(3)
        replace_dead_runners(roster, market, used)
        assert roster.total_deaths == 3

        # Kill 2 more in the following week
        for runner in roster.runners[:2]:
            runner._died_this_week = True
            runner.death_count += 1
        replace_dead_runners(roster, market, used)
        assert roster.total_deaths == 5

    def test_survivors_not_removed(self):
        """Runners without _died_this_week must remain in the roster."""
        market = make_initial_market()
        roster, used = self._roster_with_deaths(3)
        survivor_ids_before = {r.id for r in roster.runners if not getattr(r, "_died_this_week", False)}
        replace_dead_runners(roster, market, used)
        survivor_ids_after = {r.id for r in roster.runners}
        assert survivor_ids_before.issubset(survivor_ids_after)

    def test_new_recruits_have_unique_ids(self):
        """Fresh recruits must not share IDs with each other or survivors."""
        market = make_initial_market()
        roster, used = self._roster_with_deaths(3)
        replace_dead_runners(roster, market, used)
        ids = [r.id for r in roster.runners]
        assert len(ids) == len(set(ids))

    def test_zero_deaths_leaves_roster_unchanged(self):
        """No sentinel set → nothing changes, total_deaths stays 0."""
        market = make_initial_market()
        used: set[str] = set()
        roster = create_roster("TestCo", market, used)
        ids_before = [r.id for r in roster.runners]
        replace_dead_runners(roster, market, used)
        ids_after = [r.id for r in roster.runners]
        assert ids_before == ids_after
        assert roster.total_deaths == 0
