"""
Tests for runner_sim/zone_sim/extraction_ai.py and runner_sim/zone_sim/sim.py

squad_doctrine: converts a list of shell names into a Doctrine enum — the
bridge between the shell economy and actual zone behaviour.

Encounter phase: _encounter_pair / _phase_encounters — engagement consent model
(OR logic: either squad can force combat) and odd-squad orphan pairing.
"""

import pytest
from unittest.mock import patch

from runner_sim.runners import Runner
from runner_sim.zone_sim.extraction_ai import Doctrine, squad_doctrine
from runner_sim.zone_sim.sim import Squad, make_squad, _encounter_pair, _phase_encounters


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------
def _make_runner(shell: str = "Destroyer") -> Runner:
    """Minimal Runner — only fields used by _squad_breakdown."""
    return Runner(
        id=0, name="R", company_name="Co",
        combat=0.5, extraction=0.3, support=0.2,
        current_shell=shell,
    )


def _make_squad(name: str, shell: str = "Destroyer") -> Squad:
    return make_squad(name, [_make_runner(shell)])


class TestSquadDoctrine:
    def test_all_destroyer_is_greedy(self):
        assert squad_doctrine(["Destroyer", "Destroyer", "Destroyer"]) == Doctrine.GREEDY

    def test_all_thief_is_cautious(self):
        assert squad_doctrine(["Thief", "Thief", "Thief"]) == Doctrine.CAUTIOUS

    def test_all_triage_is_support(self):
        assert squad_doctrine(["Triage", "Triage", "Triage"]) == Doctrine.SUPPORT

    def test_all_vandal_is_balanced(self):
        assert squad_doctrine(["Vandal", "Vandal", "Vandal"]) == Doctrine.BALANCED

    def test_majority_wins(self):
        """Two Destroyers + one Thief → GREEDY (2 > 1)."""
        assert squad_doctrine(["Destroyer", "Destroyer", "Thief"]) == Doctrine.GREEDY

    def test_majority_wins_cautious(self):
        """Two Thieves + one Destroyer → CAUTIOUS."""
        assert squad_doctrine(["Thief", "Thief", "Destroyer"]) == Doctrine.CAUTIOUS

    def test_tiebreak_greedy_beats_cautious(self):
        """One Destroyer (GREEDY) + one Thief (CAUTIOUS) + one Vandal (BALANCED) → GREEDY wins tie."""
        result = squad_doctrine(["Destroyer", "Thief", "Vandal"])
        assert result == Doctrine.GREEDY

    def test_tiebreak_greedy_beats_support(self):
        """GREEDY beats SUPPORT in a tie."""
        assert squad_doctrine(["Destroyer", "Triage", "Vandal"]) == Doctrine.GREEDY

    def test_tiebreak_balanced_beats_cautious(self):
        """BALANCED beats CAUTIOUS when counts are tied and GREEDY is absent."""
        # Two BALANCED shells vs two CAUTIOUS shells — this needs a 4-runner squad
        # but we can test with 2 vs 2 using Vandal(BALANCED) + Rook(BALANCED) vs
        # Thief(CAUTIOUS) + Recon(CAUTIOUS). With 4 slots, counts are equal.
        # squad_doctrine has no squad-size restriction, so test directly.
        result = squad_doctrine(["Vandal", "Rook", "Thief", "Recon"])
        assert result == Doctrine.BALANCED

    def test_assassin_maps_to_greedy(self):
        """Assassin is a GREEDY shell."""
        assert squad_doctrine(["Assassin", "Assassin", "Assassin"]) == Doctrine.GREEDY

    def test_recon_maps_to_cautious(self):
        """Recon is a CAUTIOUS shell."""
        assert squad_doctrine(["Recon", "Recon", "Recon"]) == Doctrine.CAUTIOUS

    def test_rook_maps_to_balanced(self):
        """Rook is a BALANCED shell."""
        assert squad_doctrine(["Rook", "Rook", "Rook"]) == Doctrine.BALANCED


# ---------------------------------------------------------------------------
# Encounter phase
# ---------------------------------------------------------------------------
class TestEncounterPhase:
    """Tests for _encounter_pair and _phase_encounters.

    random.random and should_engage are mocked so tests are deterministic and
    don't depend on published AI tree files being present.
    """

    # ── engagement consent model (OR logic) ─────────────────────────────

    def test_aggressor_forces_combat_when_opponent_disengages(self):
        """One squad engaging is enough to trigger combat (OR, not AND)."""
        a, b = _make_squad("Alpha"), _make_squad("Bravo")
        log: list[str] = []
        pairs: list = []

        with patch("runner_sim.zone_sim.sim.random.random", return_value=0.0), \
             patch("runner_sim.zone_sim.sim.should_engage", side_effect=[True, False]):
            _encounter_pair(a, b, log, tick=1, combat_pairs=pairs)

        assert len(pairs) == 1
        assert any("forces engagement" in line for line in log)

    def test_both_engage_labels_correctly(self):
        """When both squads choose to engage, log says 'both engage'."""
        a, b = _make_squad("Alpha"), _make_squad("Bravo")
        log: list[str] = []
        pairs: list = []

        with patch("runner_sim.zone_sim.sim.random.random", return_value=0.0), \
             patch("runner_sim.zone_sim.sim.should_engage", return_value=True):
            _encounter_pair(a, b, log, tick=1, combat_pairs=pairs)

        assert len(pairs) == 1
        assert any("both engage" in line for line in log)

    def test_both_disengage_produces_no_combat(self):
        """Both squads refusing to engage → no combat, no pair."""
        a, b = _make_squad("Alpha"), _make_squad("Bravo")
        log: list[str] = []
        pairs: list = []

        with patch("runner_sim.zone_sim.sim.random.random", return_value=0.0), \
             patch("runner_sim.zone_sim.sim.should_engage", return_value=False):
            _encounter_pair(a, b, log, tick=1, combat_pairs=pairs)

        assert len(pairs) == 0
        assert any("both disengage" in line for line in log)

    def test_encounter_miss_produces_no_pair_and_no_log(self):
        """Encounter roll failure skips the pair entirely — no log entry."""
        a, b = _make_squad("Alpha"), _make_squad("Bravo")
        log: list[str] = []
        pairs: list = []

        # return value >= ENCOUNTER_BASE_PROB (0.45) → paths don't cross
        with patch("runner_sim.zone_sim.sim.random.random", return_value=0.99), \
             patch("runner_sim.zone_sim.sim.should_engage", return_value=True):
            _encounter_pair(a, b, log, tick=1, combat_pairs=pairs)

        assert pairs == []
        assert log == []

    # ── odd-squad orphan fix ─────────────────────────────────────────────

    def test_odd_squad_orphan_gets_encounter_opportunity(self):
        """Third squad in a 3-squad run is paired with squad[0] via the orphan fix.

        Before the fix: step-2 loop range(0, 2, 2) = [0] → 1 pair; squad[2] idle.
        After the fix:  _encounter_pair(active[-1], active[0]) → 2 pairs total.
        """
        squads = [_make_squad(f"S{i}") for i in range(3)]

        with patch("runner_sim.zone_sim.sim.random.random", return_value=0.0), \
             patch("runner_sim.zone_sim.sim.should_engage", return_value=True), \
             patch("runner_sim.zone_sim.sim.random.shuffle", side_effect=lambda x: None):
            pairs = _phase_encounters(squads, [], tick=1)

        # step-2: (S0, S1) → 1 pair. Orphan fix: (S2, S0) → 2nd pair.
        assert len(pairs) == 2

    def test_single_active_squad_produces_no_pairs(self):
        """Fewer than 2 active squads → empty list, no crash."""
        squads = [_make_squad("Alpha")]
        pairs = _phase_encounters(squads, [], tick=1)
        assert pairs == []

    def test_extracted_squad_excluded_from_pairing(self):
        """Extracted squads are inactive and must not appear in pairs."""
        a, b = _make_squad("Alpha"), _make_squad("Bravo")
        b.extracted = True

        pairs = _phase_encounters([a, b], [], tick=1)
        assert pairs == []

    def test_eliminated_squad_excluded_from_pairing(self):
        """Eliminated squads are inactive and must not appear in pairs."""
        a, b = _make_squad("Alpha"), _make_squad("Bravo")
        b.eliminated = True

        pairs = _phase_encounters([a, b], [], tick=1)
        assert pairs == []
