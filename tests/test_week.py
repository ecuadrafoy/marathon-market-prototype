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
