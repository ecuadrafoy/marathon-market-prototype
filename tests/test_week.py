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
    # TODO: implement these tests.
    #
    # apply_zone_outcome with squad_extracted=True, squad_eliminated=False
    # should do ALL of the following:
    #
    #   - increment extraction_attempts by 1
    #   - increment extraction_successes by 1
    #   - add credits_received to both credit_balance AND net_loot
    #   - add kills_attributed to runner.eliminations
    #   - append current_shell to shell_history
    #   - NOT set _died_this_week
    #   - apply attribute drift (runner attributes should change slightly)
    #   - apply affinity gain (runner.shell_affinities[shell] should increase)
    #
    # Hints:
    #   - Use _fresh_runner() to get a clean starting state
    #   - Drift and affinity are stochastic by nature but deterministic given
    #     fixed inputs — you can assert "changed" rather than exact values
    #   - For kills, pass kills_attributed=3 and assert runner.eliminations == 3

    def test_credits_added_to_balance(self):
        # TODO
        pass

    def test_credits_added_to_net_loot(self):
        # TODO
        pass

    def test_extraction_successes_incremented(self):
        # TODO
        pass

    def test_kills_attributed(self):
        # TODO
        pass

    def test_shell_history_appended(self):
        # TODO
        pass

    def test_died_this_week_not_set(self):
        # TODO
        pass

    def test_attributes_drift(self):
        # TODO: assert that at least one of combat/extraction/support changed
        pass

    def test_attributes_still_sum_to_one_after_drift(self):
        # TODO: drift should preserve the simplex constraint
        pass


# ---------------------------------------------------------------------------
# Branch 3: neither extracted nor eliminated (defensive fallback)
# ---------------------------------------------------------------------------
class TestApplyZoneOutcomeFallback:
    # TODO: implement these tests.
    #
    # apply_zone_outcome with squad_extracted=False, squad_eliminated=False
    # is a defensive branch that "shouldn't fire today" per the docstring.
    # But we still want to verify its contract:
    #
    #   - extraction_attempts incremented
    #   - extraction_successes NOT incremented
    #   - credit_balance unchanged (no credits received)
    #   - _died_this_week NOT set
    #   - drift IS applied (runner participated, just didn't extract)
    #   - shell_history appended

    def test_no_credits_in_fallback(self):
        # TODO
        pass

    def test_no_extraction_success_in_fallback(self):
        # TODO
        pass

    def test_not_marked_as_dead_in_fallback(self):
        # TODO
        pass
